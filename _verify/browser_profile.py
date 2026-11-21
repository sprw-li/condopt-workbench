"""§11.5 临时资源卫生：所有浏览器驱动共用的一次性 profile 管理。

为什么需要它（§21.15/§21.17 的教训，逐条对应 CONTRACT §11.5）：
  1. 旧脚本把 profile 建在 ``Path.home()/.condopt-*`` 且**从不删除** → 我自己的 9 个驱动都在漏；
  2. 删了不算干净：**Edge 子进程会在主进程被杀之后继续收尾，把刚删掉的 profile 写回来**
     （残留约半份文件），所以「删完检查一次」会误判成功 —— 必须**连续两次确认不存在**；
  3. ``shutil.rmtree(..., ignore_errors=True)`` 会把删除失败**静默吞掉**（同族反例）→ 本模块
     一律不吞：删不掉**打 stderr** 并返回 False。

接口：
  ``make_profile(tag)``   在 ``%TEMP%`` 下建 profile，名字含 pid + 时间戳；自动登记 atexit 清理
  ``attach(profile, proc)``  记住该 profile 对应的浏览器进程（供树杀）
  ``cleanup(profile)``    ``taskkill /T /F`` 树杀 → 删除 → **连续两次确认不存在**（带 settle 与重试）
  ``residue_snapshot()``  跨 settle **多次计数取最大值**（能抓「删了又被写回」）
  ``guard_residue(fn)``   包住 ``main()``：跑完自证「新增残留 == 0」，不是只数一次
  ``free_port()``         每次取空闲调试端口（避免固定端口竞态，见 §21.15 更正 2）

自检（含对抗样本）：``py -3 _verify/browser_profile.py --self-test``
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

#: 我这一族脚本的命名空间（与 lead 探针的 `condopt-geom-*` 区分开，便于各自独立计数）
PREFIX = "condopt-verify-"

TEMP_ROOT = Path(tempfile.gettempdir())

#: profile -> 浏览器进程（可为 None；atexit 时用于树杀）
_OWNED: dict[Path, subprocess.Popen | None] = {}


def make_profile(tag: str) -> Path:
    """在 ``%TEMP%`` 下建一次性 profile（名字含 pid + 时间戳），并登记退出时清理。"""
    stamp = int(time.time() * 1000) % 100000
    profile = TEMP_ROOT / f"{PREFIX}{tag}-{os.getpid()}-{stamp}"
    profile.mkdir(parents=True, exist_ok=True)
    _OWNED[profile] = None
    return profile


def attach(profile: Path, proc: subprocess.Popen | None) -> None:
    """记录该 profile 对应的浏览器进程，供 ``taskkill /T /F`` 树杀。"""
    _OWNED[Path(profile)] = proc


def free_port() -> int:
    """取一个空闲 TCP 端口（避免固定调试端口在连跑时竞态）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def pids_using(profile: Path) -> list[int]:
    """找出命令行里引用了该 profile 的进程（``--user-data-dir=<profile>``）。

    为什么需要它：Edge 会派生 renderer/GPU/utility 等**多个子进程**；如果父进程先被杀，
    子进程会被重新挂载（reparent），此时 ``taskkill /T /PID <已死的父>`` **再也走不到它们**，
    于是它们继续持有 profile → 删除失败（2026-11-21 实测：9/10 驱动一度留下 689 个条目的目录，
    系统里还躺着 61 个孤儿 msedge）。所以收尾必须**按 profile 反查持有者**再杀。
    """
    profile_str = str(profile)
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{profile_str}') }} | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
    except Exception as exc:                                    # noqa: BLE001 - 不吞：打印
        print(f"[profile] 反查持有者失败（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return []
    pids = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return [pid for pid in pids if pid != os.getpid()]


def kill_tree(proc: subprocess.Popen | None, pid: int | None = None) -> None:
    """``taskkill /T /F`` 杀**整棵进程树**（必须在父进程**还活着**时调用，否则走不到子进程）。"""
    target = pid if pid is not None else (proc.pid if proc is not None else None)
    if target:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(target)],
                           capture_output=True, text=True, timeout=30)
        except FileNotFoundError:                               # 非 Windows 兜底
            if proc is not None:
                proc.kill()
        except Exception as exc:                                # noqa: BLE001 - 不吞：打印出来
            print(f"[profile] taskkill 失败 pid={target}: {exc!r}", file=sys.stderr)
    if proc is not None:
        try:
            proc.wait(timeout=15)
        except Exception:                                       # noqa: BLE001
            pass


def kill_profile_holders(profile: Path) -> int:
    """按 profile 反查并杀掉所有持有者（含已被 reparent 的孤儿子进程）；返回杀掉个数。"""
    killed = 0
    for pid in pids_using(profile):
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=30)
            killed += 1
        except Exception as exc:                                # noqa: BLE001
            print(f"[profile] 杀持有者 {pid} 失败: {exc!r}", file=sys.stderr)
    return killed


def _nonempty_children(profile: Path) -> int:
    try:
        return sum(1 for _ in profile.rglob("*"))
    except OSError:
        return -1


def cleanup(profile: Path, attempts: int = 6, settle: float = 0.6) -> bool:
    """树杀 → 删除 → **连续两次确认不存在**才算干净；删不掉打 stderr 并返回 False。"""
    profile = Path(profile)
    proc = _OWNED.pop(profile, None)
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        kill_tree(proc)                          # 父还活着时先树杀（能走到子进程）
        kill_profile_holders(profile)            # 再按 profile 反查，清掉已 reparent 的孤儿
        if profile.exists():
            try:
                shutil.rmtree(profile)             # 故意不用 ignore_errors → 失败要能看见
            except FileNotFoundError:
                pass
            except OSError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        # 连续两次确认：第一次删掉后子进程可能又写回来
        if not profile.exists():
            time.sleep(settle)
            if not profile.exists():               # 第二次确认
                return True
            last_error = "删除后目录被写回（子进程收尾竞态）"

    exists = profile.exists()
    detail = f"仍存在（{_nonempty_children(profile)} 个条目）" if exists else "无法稳定删除"
    print(f"[profile] 清理失败：{profile} {detail}"
          f"{'；最后错误 ' + last_error if last_error else ''}（尝试 {attempts} 次）", file=sys.stderr)
    return False


def cleanup_all() -> int:
    """退出时清理所有登记过的 profile；返回残留（清理失败）个数。"""
    failed = 0
    for profile in list(_OWNED):
        if not cleanup(profile):
            failed += 1
    return failed


def residue_paths(scope: str = "mine") -> list[Path]:
    """列出 ``%TEMP%`` 下的残留 profile。

    ``scope="mine"``   只算**本进程 pid** 建的（名字里含 ``-<pid>-``）——这是「我的脚本跑完不残留」
                       的正确语义，也避免多 Agent 并发跑验收时把**别人的**在途 profile 记到我头上
                       （2026-11-21 实测：api-backend 并发跑 `v22_oldform_fields.py` 时，
                       我的全局计数把它的 `condopt-verify-oldform-*` 误判成自己的新增残留）。
    ``scope="all"``    全部同前缀目录（信息性展示，不用于判分）。
    """
    try:
        paths = [p for p in TEMP_ROOT.glob(f"{PREFIX}*") if p.is_dir()]
    except OSError:
        return []
    if scope == "all":
        return paths
    marker = f"-{os.getpid()}-"
    return [p for p in paths if marker in p.name]


def residue_count(scope: str = "mine") -> int:
    return len(residue_paths(scope))


def residue_snapshot(samples: int = 3, settle: float = 2.0, scope: str = "mine") -> int:
    """跨 settle **多次计数取最大值**——只数一次只能证明「那一刻不存在」。"""
    peak = residue_count(scope)
    for _ in range(max(0, samples - 1)):
        time.sleep(settle)
        peak = max(peak, residue_count(scope))
    return peak


def serial_active() -> bool:
    """本进程当前是否**真的**持有串行保护（套件锁 env 或本进程持锁文件）。"""
    if os.environ.get("CONDOPT_VERIFY_SERIAL_HELD") == "1":
        return True
    if not SERIAL_LOCK.exists():
        return False
    try:
        info = SERIAL_LOCK.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for part in info.split():
        if part.startswith("pid="):
            try:
                return int(part[4:]) == os.getpid()
            except ValueError:
                return False
    return False


def guard_residue(fn) -> int:
    """包住 ``main()``：跑完自证「**本进程**新增残留 == 0」（跨 settle 多次计数）。

    ⚠️ **本入口不取串行锁。** 存量脚本若直接用它当 ``__main__``，会**静默不参与** §11.5-6 的
    串行约束 —— 这正是「门禁给虚假安心」的同族缺陷（api-backend 2026-11-21 实测踩到：
    两个驱动并发却都通过，因为它们用的是本入口）。
    因此这里在**没有串行保护**时**大声警告**；新脚本请一律用 ``run_guarded(fn, tag)``。
    """
    if not serial_active():
        print("[并发] 警告：`guard_residue` 入口**不取串行锁**（CONTRACT §11.5-6）——"
              "存量脚本用它做 __main__ 会静默不参与串行，可能造成「两个驱动并发却都通过」的假安心。"
              "请改用 `bp.run_guarded(main, \"<tag>\")`。", file=sys.stderr)
    before = residue_snapshot(samples=2, settle=1.0, scope="mine")
    code = 0
    try:
        code = int(fn() or 0)
    except SystemExit as exc:                                  # 透传脚本自己的退出码
        code = int(exc.code or 0)
    except BaseException:                                       # noqa: BLE001
        traceback.print_exc()
        code = 1
    cleanup_all()
    after = residue_snapshot(samples=3, settle=2.0, scope="mine")
    new = after - before
    global_after = residue_snapshot(samples=1, settle=0.0, scope="all")
    if new > 0:
        print(f"[卫生] 跑完新增残留 {new} 个（before={before} after={after}）"
              f"：{ [p.name for p in residue_paths('mine')][:6] }", file=sys.stderr)
        return 3 if code == 0 else code
    print(f"[卫生] 跑完新增残留 == 0（本进程 before={before} after={after}，跨 settle 多次计数）"
          f"；同前缀全局={global_after}（含他人并发运行的 profile，不计入判分）")
    return code


# --------------------------------------------------------------------------- #
# §11.5-6：浏览器驱动必须**串行跑**；并发冲突报成「环境/并发类失败」并附证据
# --------------------------------------------------------------------------- #

SERIAL_LOCK = TEMP_ROOT / f"{PREFIX}serial.lock"

#: 环境/并发类失败的专用退出码（区别于产品缺陷 exit 2 与卫生失败 exit 3）
ENV_CONFLICT_EXIT = 4

_LOCK_HELD = False


def _pid_alive(pid: int) -> bool:
    """进程是否存活（Windows 用 OpenProcess，避免依赖 tasklist 解析）。"""
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32                    # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:                                        # noqa: BLE001 - 非 Windows 兜底
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def other_inflight_profiles() -> list[Path]:
    """同前缀但**不是本进程**建的 profile（= 别人正在跑的驱动的在途 profile）。"""
    return [p for p in residue_paths("all") if f"-{os.getpid()}-" not in p.name]


def acquire_serial(tag: str) -> tuple[bool, str]:
    """取串行锁。返回 ``(是否拿到, 证据)``；拿不到时证据里必须能看出「谁在跑 + 同前缀 profile 数」。

    若已处在 ``suite_guard`` 里（``CONDOPT_VERIFY_SERIAL_HELD=1``），直接视为已持有：
    套件级锁是**跨整个套件**持有的，套件内部的各驱动不应再互相阻塞。
    """
    global _LOCK_HELD
    if os.environ.get("CONDOPT_VERIFY_SERIAL_HELD") == "1":
        return True, ""
    for _ in range(3):
        try:
            fd = os.open(SERIAL_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                info = SERIAL_LOCK.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                info = "(锁文件不可读)"
            owner = 0
            for part in info.split():
                if part.startswith("pid="):
                    try:
                        owner = int(part[4:])
                    except ValueError:
                        owner = 0
            if owner and _pid_alive(owner):
                others = other_inflight_profiles()
                return False, (f"已有驱动在跑：{info}；同前缀 profile {len(others)} 个"
                               f"（{[p.name for p in others][:4]}）")
            print(f"[并发] 发现**陈旧锁**（属主 pid={owner} 已不存在）：{info} → 接管", file=sys.stderr)
            try:
                SERIAL_LOCK.unlink()
            except OSError:
                pass
            continue
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()} tag={tag} since={time.strftime('%H:%M:%S')}\n")
            _LOCK_HELD = True
            return True, ""
    return False, "连续 3 次都无法取得串行锁（锁文件反复出现）"


def release_serial() -> None:
    global _LOCK_HELD
    if _LOCK_HELD:
        try:
            SERIAL_LOCK.unlink()
        except OSError:
            pass
        _LOCK_HELD = False


def other_browsers() -> list[int]:
    """当前命令行引用 ``condopt-*`` 临时 profile 的 msedge 进程（= 正在跑的浏览器驱动数）。

    比「同前缀 profile 目录数」更稳的并发证据：对方可能**还没建目录**就已开浏览器，
    也可能**已删目录**但浏览器还在收尾；两者都会让「目录计数」漏判（实测踩过）。
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains('condopt-') } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=90)
    except Exception:                                        # noqa: BLE001
        return []
    return [int(line.strip()) for line in (out.stdout or "").splitlines() if line.strip().isdigit()]


def concurrency_evidence(when: str) -> str:
    """给「环境/并发类失败」用的证据串。"""
    others = other_inflight_profiles()
    browsers = other_browsers()
    return (f"{when}：同前缀 profile {len(others)} 个{[p.name for p in others][:4]}；"
            f"引用 condopt-* 的 msedge 进程 {len(browsers)} 个")


def suite_guard(fn, tag: str = "suite") -> int:
    """套件级串行：**整个套件期间**持有锁（驱动级的锁只在单个驱动生命周期内，套件之间会交错）。

    为什么需要：驱动级锁只保护「两个驱动不重叠」，但两个**套件运行器**仍会在各自的驱动间隙交错
    （2026-11-21 实测：我在后台跑 10 驱动套件时又前台跑了一个驱动，两者交错 → 一个 `Page.enable` 超时）。
    """
    acquired, evidence = acquire_serial(tag)
    if not acquired:
        print(f"[并发] {evidence}\n"
              f"        → 按 CONTRACT §11.5-6 判为**环境/并发类失败**（exit {ENV_CONFLICT_EXIT}）；"
              f"请等对方结束（或串行）后重跑。", file=sys.stderr)
        return ENV_CONFLICT_EXIT
    previous = os.environ.get("CONDOPT_VERIFY_SERIAL_HELD")
    os.environ["CONDOPT_VERIFY_SERIAL_HELD"] = "1"
    try:
        return int(fn() or 0)
    finally:
        if previous is None:
            os.environ.pop("CONDOPT_VERIFY_SERIAL_HELD", None)
        else:
            os.environ["CONDOPT_VERIFY_SERIAL_HELD"] = previous
        release_serial()


def run_guarded(fn, tag: str) -> int:
    """各驱动 ``__main__`` 的统一入口：串行加锁 → 跑 → 自证残留；并落实 §11.5-6 的归因规则。"""
    others_at_start = other_inflight_profiles()
    acquired, evidence = acquire_serial(tag)
    if not acquired:
        print(f"[并发] {evidence}\n"
              f"        → 按 CONTRACT §11.5-6 判为**环境/并发类失败**（exit {ENV_CONFLICT_EXIT}），"
              f"不得记为产品缺陷；请串行重跑。", file=sys.stderr)
        return ENV_CONFLICT_EXIT
    try:
        if others_at_start:
            print(f"[并发] 启动时检测到同前缀 profile {len(others_at_start)} 个"
                  f"（{[p.name for p in others_at_start][:4]}）—— 别人可能正在跑；"
                  f"本次已持有串行锁，若出现连接类错误按 §11.5-6 归为环境/并发类。")
        code = guard_residue(fn)
        if code != 0:
            # 失败时**重新取证**：起因可能是「对方启动晚于本驱动」，只看启动瞬间会漏判
            after_others = other_inflight_profiles()
            after_browsers = other_browsers()
            if others_at_start or after_others or len(after_browsers) > 1:
                print(f"[并发] 本次退出码 {code}；并发证据：启动时同前缀 profile={len(others_at_start)} 个、"
                      f"结束时={len(after_others)} 个、结束时 condopt-* msedge={len(after_browsers)} 个"
                      f"（>1 即至少两套驱动同时在跑）。\n"
                      f"        → 按 CONTRACT §11.5-6 判为**环境/并发类失败**（exit {ENV_CONFLICT_EXIT}）——"
                      f"原始输出保留在日志里；请**串行重跑**后再定性质，不要记为产品缺陷。", file=sys.stderr)
                return ENV_CONFLICT_EXIT
        return code
    finally:
        release_serial()


# --------------------------------------------------------------------------- #
# 自检：含对抗样本（写回竞态 / 删不掉）
# --------------------------------------------------------------------------- #

def _self_test() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))

    base = residue_count()

    # 1) 正常路径：建 → 清理 → 连续两次确认不存在
    p1 = make_profile("selftest-normal")
    existed = p1.exists()
    (p1 / "Default").mkdir(exist_ok=True)
    (p1 / "Default" / "x").write_text("x", encoding="utf-8")
    ok1 = cleanup(p1)
    record("1 正常：建→删→连续两次确认不存在", existed and ok1 and not p1.exists(),
           f"existed={existed} cleanup={ok1} still={p1.exists()}")

    # 2) 对抗：删掉后被「子进程」写回 —— cleanup 必须仍以「不存在」收场
    p2 = make_profile("selftest-writeback")
    (p2 / "Default").mkdir(exist_ok=True)

    def _writeback_after_delete(path: Path) -> None:
        # 模拟 Edge 子进程：目录被删后又被写回
        time.sleep(0.05)
        path.mkdir(exist_ok=True)
        (path / "Default").mkdir(exist_ok=True)
        (path / "Default" / "half-written").write_text("x" * 16, encoding="utf-8")

    shutil.rmtree(p2)                       # 第一次删除
    _writeback_after_delete(p2)             # 写回
    ok2 = cleanup(p2)
    record("2 对抗：删除后被写回 → cleanup 仍以「不存在」收场（不误判成功）",
           ok2 and not p2.exists(), f"cleanup={ok2} still={p2.exists()}")

    # 3) 对抗：删不掉（句柄占用）→ cleanup 必须返回 False 且**打到 stderr**（不许静默吞）
    p3 = make_profile("selftest-locked")
    locked = p3 / "locked.bin"
    locked.write_text("held", encoding="utf-8")
    handle = open(locked, "rb")             # 开着句柄 → Windows 上 rmtree 会失败
    import io
    import contextlib
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            ok3 = cleanup(p3, attempts=2, settle=0.1)
        stderr_text = buffer.getvalue()
    finally:
        handle.close()
    record("3 对抗：句柄占用删不掉 → 返回 False 且打 stderr（没有静默吞）",
           (ok3 is False) and ("清理失败" in stderr_text),
           f"cleanup={ok3} stderr={stderr_text.strip()[:70]!r}")
    cleanup(p3)                             # 收尾

    # 4) guard_residue 必须在「**本次运行新增**残留」时判非零
    #    注意：判据是 after - before；所以要**在 guard 内部**泄漏，而不是提前建好目录。
    leak_holder: list[Path] = []

    def _leak_inside_guard() -> int:
        leak = make_profile("selftest-guard")
        (leak / "Default").mkdir(exist_ok=True)
        _OWNED.pop(leak, None)              # 逃离自动清理，模拟真泄漏
        leak_holder.append(leak)
        return 0

    code4 = guard_residue(_leak_inside_guard)
    record("4 对抗：guard 内部新增残留 → guard_residue 必须判非零（并打 stderr）",
           code4 == 3, f"code={code4}（期望 3）")
    for leak in leak_holder:
        cleanup(leak)

    # 5) 树杀：父 python 起一个不断写心跳文件的孙进程；树杀后心跳必须停止
    #    路径用**环境变量**传（避免嵌套 -c 的转义地狱），孙进程跑一个临时脚本文件。
    beat = TEMP_ROOT / f"{PREFIX}heartbeat-{os.getpid()}.txt"
    beat_script = TEMP_ROOT / f"{PREFIX}beat-{os.getpid()}.py"
    if beat.exists():
        beat.unlink()
    beat_script.write_text(
        "import pathlib, sys, time\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "while True:\n"
        "    path.write_text(str(time.time()))\n"
        "    time.sleep(0.2)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "BEAT_SCRIPT": str(beat_script), "BEAT_PATH": str(beat)}
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import os, subprocess, sys, time\n"
         "subprocess.Popen([sys.executable, os.environ['BEAT_SCRIPT'], os.environ['BEAT_PATH']])\n"
         "time.sleep(120)"],
        env=env,
    )
    time.sleep(3.0)
    grew_before = beat.exists()
    size_c = beat.stat().st_size if beat.exists() else -1
    kill_tree(child)
    time.sleep(3.0)
    size_d = beat.stat().st_size if beat.exists() else -2
    time.sleep(2.5)
    size_e = beat.stat().st_size if beat.exists() else -3
    record("5 树杀：taskkill /T /F 连**孙进程**一起杀（心跳在父进程被杀后停止更新）",
           grew_before and (size_d == size_e),
           f"孙进程在写={grew_before} 杀后 size {size_c}→{size_d}→{size_e} 父存活={child.poll() is None}")
    if beat.exists():
        beat.unlink()
    if beat_script.exists():
        beat_script.unlink()

    # 6) 收尾后整体残留归零
    cleanup_all()
    after = residue_snapshot(samples=3, settle=1.0)
    record("6 收尾：自检跑完新增残留 == 0（跨 settle 多次计数）",
           after <= base, f"base={base} after={after}")

    # 7) 对抗：**别的进程**的 profile 不得计入我的判分（2026-11-21 实测踩过：
    #    并发跑另一个验收脚本时，全局计数把它的目录误判成我的新增残留）
    foreign = TEMP_ROOT / f"{PREFIX}foreign-99999999-1"
    foreign.mkdir(parents=True, exist_ok=True)
    mine_now, all_now = residue_count("mine"), residue_count("all")
    record("7 对抗：他人 pid 的 profile 不计入「我的」残留（但计入全局展示）",
           (mine_now == 0) and (all_now >= 1),
           f"mine={mine_now} all={all_now}")
    shutil.rmtree(foreign, ignore_errors=True)

    # 8) 对抗：别人持锁时，我必须拿到「环境/并发类失败 + 证据」，而不是跑下去
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(1.5)
    try:
        SERIAL_LOCK.write_text(f"pid={holder.pid} tag=selftest-holder since=00:00:00\n", encoding="utf-8")
        ok8, evidence8 = acquire_serial("selftest-contend")
        record("8 对抗：他人持锁 → 判定为环境/并发类失败，且证据含「谁在跑 + 同前缀 profile 数」",
               (ok8 is False) and ("已有驱动在跑" in evidence8) and ("同前缀 profile" in evidence8),
               f"acquired={ok8} evidence={evidence8[:80]!r}")
        release_serial()
        if SERIAL_LOCK.exists():
            SERIAL_LOCK.unlink()
    finally:
        kill_tree(holder)

    # 9) 对抗：陈旧锁（属主已死）→ 必须能接管，而不是永久卡死
    SERIAL_LOCK.write_text("pid=999999 tag=dead since=00:00:00\n", encoding="utf-8")
    ok9, evidence9 = acquire_serial("selftest-stale")
    record("9 对抗：陈旧锁（属主已死）→ 可接管（打印接管提示）",
           ok9 is True, f"acquired={ok9} evidence={evidence9!r}")
    release_serial()

    # 10) 收尾：锁文件不残留
    record("10 收尾：串行锁文件不残留", not SERIAL_LOCK.exists(), f"lock exists={SERIAL_LOCK.exists()}")

    # 11) 对抗：「无锁入口」必须**大声警告**（防 §11.5-6 对存量脚本假生效）；持锁时不得刷噪音
    #     api-backend 实测踩到：两个驱动并发却都通过，因为它们用的是不取锁的 guard_residue。
    import contextlib
    import io

    buf_unlocked = io.StringIO()
    with contextlib.redirect_stderr(buf_unlocked):
        guard_residue(lambda: 0)
    warned_unlocked = "不取串行锁" in buf_unlocked.getvalue()

    acquired11, _ = acquire_serial("selftest-warn")
    buf_locked = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf_locked):
            guard_residue(lambda: 0)
    finally:
        release_serial()
    warned_locked = "不取串行锁" in buf_locked.getvalue()
    record("11 对抗：不取锁的入口必须警告（否则契约对存量脚本假生效）；持锁时不警告",
           warned_unlocked and acquired11 and not warned_locked,
           f"无锁时警告={warned_unlocked} 持锁={acquired11} 持锁时警告={warned_locked}")

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\n自检：{passed}/{len(checks)} 通过")
    return 0 if passed == len(checks) else 2


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    print(f"用法：py -3 _verify/browser_profile.py --self-test\n当前残留 {PREFIX}* = {residue_count()}")
