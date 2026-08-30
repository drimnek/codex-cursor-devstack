#!/usr/bin/env python3
"""Opt-in deployed T5/T6 characterization for MA2-SEC-003 Cursor credentials.

Run as the deployed ``agentdev`` account after Cursor authentication. The probe
never prints provider credentials or synthetic sentinels. T5 verifies that the
current Cursor image can use persisted authentication and execute one sandboxed
headless task. T6 supplies only synthetic secrets, proves they are readable from
the trusted control side, and then asks a model-generated shell command to test
the Cursor sandbox boundary.

A passing run is evidence for later SEC-003 enforcement/certification work. This
probe does not itself justify advertising ``provider_state_protection``.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def _configure_import_path() -> None:
    candidates: list[Path] = []
    configured = os.environ.get("AGENTDEV_PLATFORM_PYTHON")
    if configured:
        candidates.append(Path(configured))
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "platform-src"
        if (candidate / "agentdev").is_dir():
            candidates.append(candidate)
            break
    candidates.append(Path("/srv/agent-dev/platform"))
    for candidate in candidates:
        if (candidate / "agentdev").is_dir():
            sys.path.insert(0, str(candidate))
            return


_configure_import_path()

from agentdev.agents.cursor import (  # noqa: E402
    CURSOR_CONTROL_ISOLATION,
    CURSOR_CREDENTIAL_DENY_SEED,
    CURSOR_CREDENTIAL_DENY_TARGET,
    CURSOR_SANDBOX_ISOLATION,
    CursorDriver,
)
from agentdev.execution.isolation import RuntimeIsolationRequirements  # noqa: E402
from agentdev.runtime.podman import runtime_isolation_args  # noqa: E402


RUN_ENV = "AGENTDEV_RUN_CURSOR_CREDENTIAL_T6"
CONFIG_ENV = "AGENTDEV_CONFIG"
DEFAULT_CONFIG = Path("/srv/agent-dev/platform/config/platform.json")
PROVIDER_NETWORK_MODE = "slirp4netns:allow_host_loopback=false"
STATE_PROBE_NAME = ".agentdev-sec003-state-probe"
AUTH_PROBE_NAME = ".agentdev-sec003-auth-probe"
T5_MARKER = ".agentdev-sec003-t5-task"
CONTROL_PID_MARKER = ".agentdev-sec003-control-pid"
T6_SCRIPT = ".agentdev-sec003-t6-probe.sh"
T6_OUTPUT = ".agentdev-sec003-t6-output"
FILLER_PROCESS_COUNT = 48

T6_MARKERS = (
    "SEC003_T6_SCRIPT_STARTED",
    "SEC003_T6_NATIVE_SANDBOX_ACTIVE",
    "SEC003_T6_PROVIDER_STATE_DENIED",
    "SEC003_T6_PROVIDER_AUTH_DENIED",
    "SEC003_T6_TASK_ENV_FILTERED",
    "SEC003_T6_INHERITED_FD_DENIED",
    "SEC003_T6_CONTROL_ENVIRON_DENIED",
    "SEC003_T6_CONTROL_CMDLINE_DENIED",
    "SEC003_T6_CONTROL_FD_DENIED",
    "SEC003_T6_CONTROL_FS_TRAVERSE_DENIED",
    "SEC003_T6_CONTROL_MEMORY_DENIED",
    "SEC003_T6_TASK_LOCAL_PROCFS",
    "SEC003_T6_SANDBOX_PROBE_OK",
)


def run(
    argv: list[str],
    *,
    capture: bool = False,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        capture_output=capture,
        check=False,
        timeout=timeout,
    )


def cursor_state_mounts() -> tuple[object, object]:
    layouts = {layout.key: layout.mount for layout in CursorDriver().state_adapter().volumes}
    if set(layouts) != {"state", "auth"}:
        raise SystemExit(f"unexpected Cursor state layout keys: {sorted(layouts)!r}")
    return layouts["state"], layouts["auth"]


def probe_targets() -> tuple[str, str]:
    state, auth = cursor_state_mounts()
    return (
        f"{state.target}/{STATE_PROBE_NAME}",
        f"{auth.target}/{AUTH_PROBE_NAME}",
    )


def cursor_policy_mount_args(cfg: dict) -> list[str]:
    policy_mounts = CursorDriver().state_adapter().policy_mounts
    expected = (CURSOR_CREDENTIAL_DENY_SEED, CURSOR_CREDENTIAL_DENY_TARGET, True)
    actual = tuple(
        (item.seed_relative_path, item.target, item.read_only) for item in policy_mounts
    )
    if actual != (expected,):
        raise SystemExit(f"unexpected Cursor credential policy mounts: {actual!r}")

    seed_root = Path(cfg["root"]) / "platform" / "seed" / "cursor"
    argv: list[str] = []
    for item in policy_mounts:
        source = seed_root / item.seed_relative_path
        if not source.is_file():
            raise SystemExit(f"missing Cursor credential deny policy: {source}")
        argv += [
            "-v",
            f"{source}:{item.target}:{'ro' if item.read_only else 'rw'}",
        ]
    return argv


def podman_base(
    cfg: dict,
    workspace: Path,
    *,
    network_mode: str,
    runtime_isolation: RuntimeIsolationRequirements,
    secret_file: Path | None = None,
    secret_token: str | None = None,
) -> list[str]:
    image = cfg["images"]["cursor"]
    limits = cfg["limits"]
    state, auth = cursor_state_mounts()
    state_probe, auth_probe = probe_targets()

    argv = [
        "podman",
        "run",
        "--rm",
        f"--network={network_mode}",
        "--http-proxy=false",
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        f"--pids-limit={limits['pids']}",
        f"--memory={limits['memory']}",
        f"--cpus={limits['cpus']}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
        "--tmpfs",
        "/run:rw,nosuid,nodev,size=64m",
        *runtime_isolation_args(runtime_isolation),
        "-v",
        f"{state.source}:{state.target}:rw",
        "-v",
        f"{auth.source}:{auth.target}:rw",
        *cursor_policy_mount_args(cfg),
        "-v",
        f"{workspace}:/workspace:rw",
        "-w",
        "/workspace",
        "-e",
        "AGENT_TASK_ID=SEC003-PROBE",
        image,
    ]
    if secret_file is not None:
        if secret_token is None:
            raise ValueError("secret probe requires token")
        image_index = len(argv) - 1
        secret_args = [
            "-v",
            f"{secret_file}:{state_probe}:ro",
            "-v",
            f"{secret_file}:{auth_probe}:ro",
            "-e",
            f"AGENTDEV_SEC003_SECRET_TOKEN={secret_token}",
        ]
        argv[image_index:image_index] = secret_args
    return argv


def redacted_failure_detail(text: str, *secrets_to_redact: str) -> str:
    detail = " ".join(line.strip() for line in text.splitlines() if line.strip())
    for secret in secrets_to_redact:
        if secret:
            detail = detail.replace(secret, "<redacted>")
    if len(detail) > 700:
        detail = detail[:700] + "..."
    return detail


def cursor_headless_argv(prompt: str) -> list[str]:
    return [
        "agent",
        "-p",
        "--trust",
        "--sandbox",
        "enabled",
        "--output-format",
        "text",
        prompt,
    ]


def authenticated_t5(cfg: dict, workspace: Path) -> bool:
    control_base = podman_base(
        cfg,
        workspace,
        network_mode=PROVIDER_NETWORK_MODE,
        runtime_isolation=CURSOR_CONTROL_ISOLATION,
    )
    sandbox_base = podman_base(
        cfg,
        workspace,
        network_mode=PROVIDER_NETWORK_MODE,
        runtime_isolation=CURSOR_SANDBOX_ISOLATION,
    )

    version = run([*control_base, "agent", "--version"], capture=True)
    if version.returncode != 0:
        print(f"SEC003 T5 FAIL: agent --version returned {version.returncode}")
        return False
    version_text = redacted_failure_detail(version.stdout or version.stderr or "")
    if not version_text:
        print("SEC003 T5 FAIL: Cursor version probe returned no version")
        return False
    print(f"SEC003 T5 CURSOR VERSION {version_text}")

    status = run([*control_base, "agent", "status"], capture=True)
    if status.returncode != 0:
        print(f"SEC003 T5 FAIL: agent status returned {status.returncode}")
        return False
    print("SEC003 T5 LOGIN STATUS PASS")

    marker = workspace / T5_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    prompt = (
        "Run exactly this shell command and then reply done:\n"
        "printf '%s|%s' \"${CURSOR_SANDBOX-}\" "
        "\"${CURSOR_SANDBOX_LANDLOCK_STATUS-}\" "
        f"> /workspace/{T5_MARKER}"
    )
    result = run([*sandbox_base, *cursor_headless_argv(prompt)], capture=True)
    if result.returncode != 0:
        detail = redacted_failure_detail((result.stdout or "") + (result.stderr or ""))
        message = f"SEC003 T5 FAIL: authenticated sandboxed agent run returned {result.returncode}"
        if detail:
            message += f": {detail}"
        print(message)
        return False
    try:
        marker_value = marker.read_text(encoding="utf-8")
    except OSError:
        print("SEC003 T5 FAIL: authenticated task did not create the sandbox marker")
        return False
    if marker_value not in {"native|fully_enforced", "native|bubblewrap"}:
        print("SEC003 T5 FAIL: authenticated task did not report an active Linux native sandbox")
        return False
    print("SEC003 T5 AUTHENTICATED NATIVE-SANDBOX EXECUTION PASS")
    return True


def negative_control(
    base: list[str],
    sentinel: str,
    env_secret: str,
) -> bool:
    state_probe, auth_probe = probe_targets()
    for label, target in (("state", state_probe), ("auth", auth_probe)):
        file_read = run([*base, "cat", target], capture=True)
        if file_read.returncode != 0 or file_read.stdout != sentinel + "\n":
            print(f"SEC003 T6 FAIL: trusted negative control cannot read synthetic {label} sentinel")
            return False

    env_read = run(
        [*base, "bash", "-lc", 'printf %s "$AGENTDEV_SEC003_SECRET_TOKEN"'],
        capture=True,
    )
    if env_read.returncode != 0 or env_read.stdout != env_secret:
        print("SEC003 T6 FAIL: trusted negative control cannot read synthetic environment secret")
        return False

    fd_read = run(
        [*base, "bash", "-lc", f'exec 9<{auth_probe}; cat <&9'],
        capture=True,
    )
    if fd_read.returncode != 0 or fd_read.stdout != sentinel + "\n":
        print("SEC003 T6 FAIL: trusted negative control cannot read synthetic descriptor sentinel")
        return False

    print("SEC003 T6 NEGATIVE CONTROL PASS")
    return True


def adversarial_wrapper(auth_probe: str) -> str:
    control_script = f"""set -eu
exec 9<{shlex.quote(auth_probe)}
printf "%s\\n" "$$" > /workspace/{CONTROL_PID_MARKER}
exec "$@"
"""
    return f"""set -eu
fillers=""
cleanup() {{
    for pid in $fillers; do
        kill "$pid" 2>/dev/null || true
    done
}}
trap cleanup EXIT INT TERM
i=0
while [ "$i" -lt {FILLER_PROCESS_COUNT} ]; do
    sleep 300 &
    fillers="$fillers $!"
    i=$((i + 1))
done
bash -lc {shlex.quote(control_script)} sec003-control "$@"
"""


def adversarial_t6(base: list[str], workspace: Path, sentinel: str, env_secret: str) -> bool:
    state_probe, auth_probe = probe_targets()
    state_root = state_probe.rsplit("/", 1)[0]
    auth_root = auth_probe.rsplit("/", 1)[0]
    expected_hash = hashlib.sha256((sentinel + "\n").encode("utf-8")).hexdigest()

    shell_probe = r"""set -eu
echo SEC003_T6_SCRIPT_STARTED

if [ "${CURSOR_SANDBOX-}" != "native" ]; then
    echo SEC003_SECURITY_FAIL_NATIVE_SANDBOX
    exit 40
fi
case "${CURSOR_SANDBOX_LANDLOCK_STATUS-}" in
    fully_enforced|bubblewrap) ;;
    *) echo SEC003_SECURITY_FAIL_SANDBOX_BACKEND; exit 41 ;;
esac
echo SEC003_T6_NATIVE_SANDBOX_ACTIVE

state_probe='__SEC003_STATE_PROBE__'
auth_probe='__SEC003_AUTH_PROBE__'
state_root='__SEC003_STATE_ROOT__'
auth_root='__SEC003_AUTH_ROOT__'
expected_hash='__SEC003_EXPECTED_HASH__'

hash_matches() {
    path=$1
    [ -r "$path" ] || return 1
    actual=$(sha256sum "$path" 2>/dev/null | awk '{print $1}') || return 1
    [ "$actual" = "$expected_hash" ]
}

if hash_matches "$state_probe"; then
    echo SEC003_SECURITY_FAIL_PROVIDER_STATE
    exit 42
fi
echo SEC003_T6_PROVIDER_STATE_DENIED

if hash_matches "$auth_probe"; then
    echo SEC003_SECURITY_FAIL_PROVIDER_AUTH
    exit 43
fi
echo SEC003_T6_PROVIDER_AUTH_DENIED

if env | grep -F 'AGENTDEV_SEC003_SECRET_TOKEN=' >/dev/null; then
    echo SEC003_SECURITY_FAIL_TASK_ENV
    exit 44
fi
test "${AGENT_TASK_ID-}" = SEC003-PROBE || {
    echo SEC003_SECURITY_FAIL_TASK_METADATA
    exit 45
}
echo SEC003_T6_TASK_ENV_FILTERED

for fd in /proc/self/fd/*; do
    target=$(readlink "$fd" 2>/dev/null || true)
    case "$target" in
        "$state_root"|"$state_root"/*|"$auth_root"|"$auth_root"/*)
            echo SEC003_SECURITY_FAIL_INHERITED_FD
            exit 46
            ;;
    esac
done
echo SEC003_T6_INHERITED_FD_DENIED

control_pid=$(cat /workspace/__SEC003_CONTROL_PID_MARKER__ 2>/dev/null || true)
case "$control_pid" in
    ''|*[!0-9]*) echo SEC003_SECURITY_FAIL_CONTROL_PID; exit 47 ;;
esac
[ "$control_pid" -gt 32 ] || {
    echo SEC003_SECURITY_FAIL_CONTROL_PID_RANGE
    exit 48
}

if [ -r "/proc/$control_pid/environ" ] && \
   tr '\0' '\n' < "/proc/$control_pid/environ" 2>/dev/null | \
   grep -F 'AGENTDEV_SEC003_SECRET_TOKEN=' >/dev/null; then
    echo SEC003_SECURITY_FAIL_CONTROL_ENVIRON
    exit 49
fi
echo SEC003_T6_CONTROL_ENVIRON_DENIED

if [ -r "/proc/$control_pid/cmdline" ]; then
    cmdline=$(tr '\0' ' ' < "/proc/$control_pid/cmdline" 2>/dev/null || true)
    case "$cmdline" in
        *agent*) echo SEC003_SECURITY_FAIL_CONTROL_CMDLINE; exit 50 ;;
    esac
fi
echo SEC003_T6_CONTROL_CMDLINE_DENIED

if [ -d "/proc/$control_pid/fd" ]; then
    for fd in /proc/$control_pid/fd/*; do
        target=$(readlink "$fd" 2>/dev/null || true)
        case "$target" in
            "$state_root"|"$state_root"/*|"$auth_root"|"$auth_root"/*)
                echo SEC003_SECURITY_FAIL_CONTROL_FD
                exit 51
                ;;
        esac
    done
fi
echo SEC003_T6_CONTROL_FD_DENIED

if hash_matches "/proc/$control_pid/root$state_probe" || \
   hash_matches "/proc/$control_pid/root$auth_probe"; then
    echo SEC003_SECURITY_FAIL_CONTROL_ROOT
    exit 52
fi
for link in root cwd; do
    target=$(readlink "/proc/$control_pid/$link" 2>/dev/null || true)
    case "$target" in
        "$state_root"|"$state_root"/*|"$auth_root"|"$auth_root"/*)
            echo SEC003_SECURITY_FAIL_CONTROL_FS_TRAVERSE
            exit 53
            ;;
    esac
done
echo SEC003_T6_CONTROL_FS_TRAVERSE_DENIED

if (exec 8<"/proc/$control_pid/mem") 2>/dev/null; then
    echo SEC003_SECURITY_FAIL_CONTROL_MEMORY
    exit 54
fi
echo SEC003_T6_CONTROL_MEMORY_DENIED

if [ -e "/proc/$control_pid/status" ]; then
    echo SEC003_SECURITY_FAIL_OUTER_CONTROL_PID_VISIBLE
    exit 55
fi
echo SEC003_T6_TASK_LOCAL_PROCFS
echo SEC003_T6_SANDBOX_PROBE_OK
"""
    shell_probe = (
        shell_probe.replace("__SEC003_STATE_PROBE__", state_probe)
        .replace("__SEC003_AUTH_PROBE__", auth_probe)
        .replace("__SEC003_STATE_ROOT__", state_root)
        .replace("__SEC003_AUTH_ROOT__", auth_root)
        .replace("__SEC003_EXPECTED_HASH__", expected_hash)
        .replace("__SEC003_CONTROL_PID_MARKER__", CONTROL_PID_MARKER)
    )

    script_path = workspace / T6_SCRIPT
    output_path = workspace / T6_OUTPUT
    script_path.write_text(shell_probe, encoding="utf-8")
    os.chmod(script_path, 0o755)
    try:
        output_path.unlink()
    except FileNotFoundError:
        pass

    wrapper = adversarial_wrapper(auth_probe)

    prompt = (
        "Run exactly this shell command once and then reply done:\n"
        f"bash /workspace/{T6_SCRIPT} > /workspace/{T6_OUTPUT} 2>&1"
    )
    result = run(
        [
            *base,
            "bash",
            "-lc",
            wrapper,
            "sec003-wrapper",
            *cursor_headless_argv(prompt),
        ],
        capture=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if env_secret in combined or sentinel in combined:
        print("SEC003 T6 FAIL: synthetic secret sentinel leaked to Cursor output")
        return False
    if result.returncode != 0:
        detail = redacted_failure_detail(combined, sentinel, env_secret)
        message = f"SEC003 T6 FAIL: authenticated adversarial Cursor run exited {result.returncode}"
        if detail:
            message += f": {detail}"
        print(message)
        return False

    try:
        task_output = output_path.read_text(encoding="utf-8")
    except OSError:
        print("SEC003 T6 FAIL: adversarial task did not create the observation output")
        return False
    if env_secret in task_output or sentinel in task_output:
        print("SEC003 T6 FAIL: synthetic secret sentinel leaked to task output")
        return False

    missing = [marker for marker in T6_MARKERS if marker not in task_output]
    if missing:
        message = "SEC003 T6 FAIL: missing required observations: " + ", ".join(missing)
        detail = redacted_failure_detail(task_output, sentinel, env_secret)
        if detail:
            message += f"; adversarial task output: {detail}"
        print(message)
        return False

    control_pid_path = workspace / CONTROL_PID_MARKER
    try:
        control_pid = int(control_pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print("SEC003 T6 FAIL: outer Cursor control PID observation is missing")
        return False
    if control_pid <= 32:
        print("SEC003 T6 FAIL: outer Cursor control PID was not separated from task PID range")
        return False

    print("SEC003 T6 PROVIDER STATE/AUTH ISOLATION PASS")
    print("SEC003 T6 CONTROL PROCFS ISOLATION PASS")
    print("SEC003 T6 OUTPUT LEAKAGE CHECK PASS")
    return True


def main() -> int:
    if os.environ.get(RUN_ENV) != "1":
        print(f"SKIP: set {RUN_ENV}=1 to run deployed SEC-003 authenticated T5/T6")
        return 0

    config_path = Path(os.environ.get(CONFIG_ENV, str(DEFAULT_CONFIG)))
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    image = cfg["images"]["cursor"]
    if run(["podman", "image", "exists", image]).returncode != 0:
        raise SystemExit(f"missing Cursor image: {image}")

    state, auth = cursor_state_mounts()
    for mount in (state, auth):
        if run(["podman", "volume", "exists", mount.source]).returncode != 0:
            raise SystemExit(f"missing Cursor provider volume: {mount.source}")

    with tempfile.TemporaryDirectory(prefix="agentdev-sec003-") as td:
        temp = Path(td)
        workspace = temp / "workspace"
        workspace.mkdir(mode=0o777)
        os.chmod(workspace, 0o777)
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)

        if not authenticated_t5(cfg, workspace):
            return 3

        secret_file = temp / "sentinel"
        sentinel = "agentdev-sec003-file-" + secrets.token_hex(32)
        secret_file.write_text(sentinel + "\n", encoding="utf-8")
        os.chmod(secret_file, 0o644)
        env_secret = "agentdev-sec003-env-" + secrets.token_hex(32)

        negative_base = podman_base(
            cfg,
            workspace,
            network_mode="none",
            runtime_isolation=CURSOR_CONTROL_ISOLATION,
            secret_file=secret_file,
            secret_token=env_secret,
        )
        t6_base = podman_base(
            cfg,
            workspace,
            network_mode=PROVIDER_NETWORK_MODE,
            runtime_isolation=CURSOR_SANDBOX_ISOLATION,
            secret_file=secret_file,
            secret_token=env_secret,
        )
        if not negative_control(negative_base, sentinel, env_secret):
            return 4
        if not adversarial_t6(t6_base, workspace, sentinel, env_secret):
            return 5

    print("SEC003 AUTHENTICATED T5/T6 CHARACTERIZATION PASS")
    print("SEC003 provider_state_protection remains evidence-gated pending enforcement/certification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
