#!/usr/bin/env python3
"""Opt-in deployed T5/T6 proof for MA2-SEC-007 Cursor task-shell egress.

Run as the deployed ``agentdev`` account after Cursor authentication. Public
fixtures are supplied by the operator. The probe evaluates the provider-neutral
MA2-SEC-005 contract through the authenticated Cursor headless/native-sandbox
path and adds Cursor-specific checks for built-in default-domain leakage and
per-repository sandbox-policy widening.

A passing run is evidence for the later SEC-007 certification commit. This probe
does not itself advertise ``network_deny``, ``network_allowlist``, or the
``hardened`` security class.
"""
from __future__ import annotations

import ipaddress
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def _configure_import_paths() -> None:
    platform_candidates: list[Path] = []
    contract_candidates: list[Path] = []

    configured_platform = os.environ.get("AGENTDEV_PLATFORM_PYTHON")
    if configured_platform:
        platform_candidates.append(Path(configured_platform))
    configured_contracts = os.environ.get("AGENTDEV_TESTS_PYTHON")
    if configured_contracts:
        contract_candidates.append(Path(configured_contracts))

    for parent in Path(__file__).resolve().parents:
        if (parent / "platform-src/agentdev").is_dir():
            platform_candidates.append(parent / "platform-src")
        if (parent / "contracts/task_egress.py").is_file():
            contract_candidates.append(parent)
        if (parent / "tests/contracts/task_egress.py").is_file():
            contract_candidates.append(parent / "tests")

    platform_candidates.append(Path("/srv/agent-dev/platform"))

    for candidate in platform_candidates:
        if (candidate / "agentdev").is_dir():
            sys.path.insert(0, str(candidate))
            break
    else:
        raise SystemExit("cannot locate deployed agentdev Python package")

    for candidate in contract_candidates:
        if (candidate / "contracts/task_egress.py").is_file():
            sys.path.insert(0, str(candidate))
            break
    else:
        raise SystemExit(
            "cannot locate SEC-005 task-egress contract; stage tests/contracts "
            "beside this probe or set AGENTDEV_TESTS_PYTHON"
        )


_configure_import_paths()

from agentdev.agents.cursor import (  # noqa: E402
    CURSOR_CONTROL_ISOLATION,
    CURSOR_CREDENTIAL_DENY_SEED,
    CURSOR_CREDENTIAL_DENY_TARGET,
    CURSOR_SANDBOX_ISOLATION,
    CURSOR_SANDBOX_POLICY_TARGET,
    CursorDriver,
    cursor_task_egress_sandbox_json,
)
from agentdev.execution.isolation import RuntimeIsolationRequirements  # noqa: E402
from agentdev.runtime.podman import runtime_isolation_args  # noqa: E402
from contracts.task_egress import (  # noqa: E402
    ADDRESS_IPV6,
    EgressProbeObservation,
    PROBE_ALLOWED_DESTINATION,
    PROBE_DENIED_PUBLIC,
    PROBE_IPV6_LINK_LOCAL,
    PROBE_IPV6_LOOPBACK,
    PROBE_IPV6_PRIVATE,
    PROBE_IPV6_PUBLIC,
    PROBE_IPV6_RAW_IP_BYPASS,
    PROBE_LOOPBACK_IPV4,
    PROBE_METADATA_IPV4,
    PROBE_NON_ALLOWLISTED_DESTINATION,
    PROBE_PRIVATE_IPV4,
    PROBE_PROVIDER_CONTROL_CONNECTIVITY,
    PROBE_RAW_IP_BYPASS,
    PROBE_REDIRECT_BYPASS,
    PROBE_SPEC_BY_ID,
    evaluate_task_egress_contract,
    task_egress_contract,
)

RUN_ENV = "AGENTDEV_RUN_CURSOR_EGRESS_T6"
CONFIG_ENV = "AGENTDEV_CONFIG"
EXPECTED_VERSION_ENV = "AGENTDEV_CURSOR_EGRESS_EXPECTED_VERSION"
ALLOWED_URL_ENV = "AGENTDEV_CURSOR_EGRESS_ALLOWED_URL"
DENIED_URL_ENV = "AGENTDEV_CURSOR_EGRESS_DENIED_URL"
REDIRECT_URL_ENV = "AGENTDEV_CURSOR_EGRESS_REDIRECT_URL"
RAW_IP_URL_ENV = "AGENTDEV_CURSOR_EGRESS_RAW_IP_URL"
DEFAULTS_URL_ENV = "AGENTDEV_CURSOR_EGRESS_DEFAULTS_URL"
IPV6_DENIED_URL_ENV = "AGENTDEV_CURSOR_EGRESS_IPV6_DENIED_URL"
IPV6_RAW_IP_URL_ENV = "AGENTDEV_CURSOR_EGRESS_IPV6_RAW_IP_URL"
IPV6_UNSUPPORTED_REASON_ENV = "AGENTDEV_CURSOR_EGRESS_IPV6_UNSUPPORTED_REASON"

DEFAULT_CONFIG = Path("/srv/agent-dev/platform/config/platform.json")
EXPECTED_CURSOR_VERSION = "2026.08.11-e8db854"
EXPECTED_NETWORK_ACCESS = "user_config_only"
PROVIDER_NETWORK_MODE = "slirp4netns:allow_host_loopback=false"

TASK_SCRIPT = ".agentdev-sec007-egress-probe.py"
TASK_OBSERVATION_HANDOFF = "/tmp/.agentdev-sec007-task-observation"
PRIVATE_IP_HANDOFF = "/tmp/.agentdev-sec007-private-ip"
LOCAL_CONTROL_PASS = "SEC007_LOCAL_CONTROL_PASS"
OBS_PREFIX = "SEC007_OBS:"
DEFAULTS_PREFIX = "SEC007_DEFAULTS:"
PROJECT_POLICY_MARKER = "SEC007 T6 PROJECT-SANDBOX WIDENING ATTEMPT INSTALLED"

PROBE_CURSOR_DEFAULTS_BYPASS = "cursor.network.defaults_bypass"

COMPACT_PROBE_KEYS = {
    "P": PROBE_DENIED_PUBLIC,
    "A": PROBE_ALLOWED_DESTINATION,
    "N": PROBE_NON_ALLOWLISTED_DESTINATION,
    "L": PROBE_LOOPBACK_IPV4,
    "I": PROBE_PRIVATE_IPV4,
    "M": PROBE_METADATA_IPV4,
    "R": PROBE_RAW_IP_BYPASS,
    "U": PROBE_IPV6_PUBLIC,
    "V": PROBE_IPV6_LOOPBACK,
    "W": PROBE_IPV6_PRIVATE,
    "X": PROBE_IPV6_LINK_LOCAL,
    "Y": PROBE_IPV6_RAW_IP_BYPASS,
    "D": PROBE_REDIRECT_BYPASS,
    "Z": PROBE_CURSOR_DEFAULTS_BYPASS,
}


class ObservationAdapter:
    def __init__(self, observations: dict[str, EgressProbeObservation]) -> None:
        self._observations = observations

    def observation(self, probe_id: str) -> EgressProbeObservation | None:
        return self._observations.get(probe_id)


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


def redacted_detail(text: str) -> str:
    detail = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(detail) > 1400:
        detail = detail[:700] + " ... [truncated] ... " + detail[-700:]
    return detail


def require_url(
    name: str,
    *,
    require_hostname: bool = False,
    raw_ip_version: int | None = None,
) -> tuple[str, str]:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} must name an http/https endpoint")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit(f"{name} must be an absolute http/https URL")
    if parsed.username or parsed.password:
        raise SystemExit(f"{name} must not contain URL credentials")

    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if require_hostname and address is not None:
        raise SystemExit(f"{name} must use a hostname, not an IP literal")
    if raw_ip_version is not None:
        if address is None or address.version != raw_ip_version:
            raise SystemExit(f"{name} must use a raw IPv{raw_ip_version} literal")
    return value, host


def ipv6_fixtures() -> tuple[str | None, str | None, str | None]:
    denied = os.environ.get(IPV6_DENIED_URL_ENV, "").strip()
    raw = os.environ.get(IPV6_RAW_IP_URL_ENV, "").strip()
    unsupported = os.environ.get(IPV6_UNSUPPORTED_REASON_ENV, "").strip()

    if denied or raw:
        if unsupported:
            raise SystemExit(
                f"{IPV6_UNSUPPORTED_REASON_ENV} cannot be combined with IPv6 fixtures"
            )
        if not denied or not raw:
            raise SystemExit(
                f"{IPV6_DENIED_URL_ENV} and {IPV6_RAW_IP_URL_ENV} "
                "must be supplied together"
            )
        denied_url, _ = require_url(IPV6_DENIED_URL_ENV, require_hostname=True)
        raw_url, _ = require_url(IPV6_RAW_IP_URL_ENV, raw_ip_version=6)
        return denied_url, raw_url, None

    if not unsupported:
        raise SystemExit(
            "supply controlled IPv6 public/raw-IP fixtures or explicitly set "
            f"{IPV6_UNSUPPORTED_REASON_ENV}"
        )
    return None, None, unsupported


def cursor_state_mounts() -> tuple[object, object]:
    layouts = {
        layout.key: layout.mount
        for layout in CursorDriver().state_adapter().volumes
    }
    if set(layouts) != {"state", "auth"}:
        raise SystemExit(f"unexpected Cursor state layout keys: {sorted(layouts)!r}")
    return layouts["state"], layouts["auth"]


def cursor_policy_mount_args(cfg: dict) -> list[str]:
    policy_mounts = CursorDriver().state_adapter().policy_mounts
    expected = (CURSOR_CREDENTIAL_DENY_SEED, CURSOR_CREDENTIAL_DENY_TARGET, True)
    actual = tuple(
        (item.seed_relative_path, item.target, item.read_only)
        for item in policy_mounts
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
    runtime_isolation: RuntimeIsolationRequirements,
    sandbox_policy: Path | None = None,
) -> list[str]:
    image = cfg["images"]["cursor"]
    limits = cfg["limits"]
    state, auth = cursor_state_mounts()

    argv = [
        "podman",
        "run",
        "--rm",
        f"--network={PROVIDER_NETWORK_MODE}",
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
        "AGENT_TASK_ID=SEC007-PROBE",
    ]
    if sandbox_policy is not None:
        argv += [
            "-v",
            f"{sandbox_policy}:{CURSOR_SANDBOX_POLICY_TARGET}:ro",
        ]
    argv.append(image)
    return argv


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


def trusted_url_control(
    base: list[str],
    label: str,
    url: str,
    *,
    ipv6: bool = False,
) -> bool:
    curl = ["curl"]
    if ipv6:
        curl.append("-6")
    result = run(
        [
            *base,
            *curl,
            "--noproxy",
            "*",
            "-fsSL",
            "--connect-timeout",
            "5",
            "--max-time",
            "20",
            url,
        ],
        capture=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"SEC007 T6 FAIL: trusted control cannot reach {label}")
        return False
    print(f"SEC007 T6 NEGATIVE CONTROL {label.upper()} PASS")
    return True


def trusted_redirect_control(
    base: list[str],
    redirect_url: str,
    denied_host: str,
) -> bool:
    result = run(
        [
            *base,
            "curl",
            "--noproxy",
            "*",
            "-fsSL",
            "-o",
            "/dev/null",
            "-w",
            "%{url_effective}",
            "--connect-timeout",
            "5",
            "--max-time",
            "20",
            redirect_url,
        ],
        capture=True,
        timeout=30,
    )
    if result.returncode != 0:
        print("SEC007 T6 FAIL: trusted redirect fixture is not reachable")
        return False
    effective_host = (urlsplit(result.stdout.strip()).hostname or "").lower().rstrip(".")
    if effective_host != denied_host:
        print("SEC007 T6 FAIL: redirect fixture does not terminate at denied host")
        return False
    print("SEC007 T6 NEGATIVE CONTROL REDIRECT FIXTURE PASS")
    return True



def write_policy_file(
    root: Path,
    *,
    workspace_access: str,
    mode: str,
    destinations: tuple[str, ...] = (),
) -> Path:
    path = root / f"sandbox-{workspace_access}-{mode}.json"
    path.write_text(
        cursor_task_egress_sandbox_json(
            workspace_access=workspace_access,
            mode=mode,
            destinations=destinations,
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o644)
    return path


def write_project_policy_widening(workspace: Path, denied_host: str) -> None:
    project_dir = workspace / ".cursor"
    project_dir.mkdir(parents=True, exist_ok=True)
    policy = project_dir / "sandbox.json"
    policy.write_text(
        json.dumps(
            {
                "networkPolicy": {
                    "default": "allow",
                    "allow": [denied_host],
                }
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(PROJECT_POLICY_MARKER)


def task_probe_source(
    *,
    denied_url: str,
    allowed_url: str,
    raw_ipv4_url: str,
    redirect_url: str,
    defaults_url: str,
    ipv6_public_url: str | None,
    ipv6_raw_url: str | None,
) -> str:
    values = {
        "denied_url": denied_url,
        "allowed_url": allowed_url,
        "raw_ipv4_url": raw_ipv4_url,
        "redirect_url": redirect_url,
        "defaults_url": defaults_url,
        "ipv6_public_url": ipv6_public_url,
        "ipv6_raw_url": ipv6_raw_url,
    }
    return f"""#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

VALUES = {values!r}
PRIVATE_IP_HANDOFF = {PRIVATE_IP_HANDOFF!r}
OBS_PREFIX = {OBS_PREFIX!r}
DEFAULTS_PREFIX = {DEFAULTS_PREFIX!r}

def reachable(url, *, ipv6=False, follow=False):
    if not url:
        return None
    argv = ["curl"]
    if ipv6:
        argv.append("-6")
    argv += [
        "--noproxy", "*",
        "-fsS",
        "--connect-timeout", "3",
        "--max-time", "8",
    ]
    if follow:
        argv.append("-L")
    argv.append(url)
    result = subprocess.run(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0

def private_url():
    try:
        value = open(PRIVATE_IP_HANDOFF, encoding="utf-8").read().strip()
    except OSError:
        return None
    if not value:
        return None
    return "http://" + value + ":18080/"

def record(vector, key, state):
    if state is None:
        marker = "e"
    else:
        marker = "1" if state else "0"
    vector.append(key + marker)

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {{"review", "implement", "dependency"}}:
        raise SystemExit(64)
    profile = sys.argv[1]
    vector = []
    if profile in {{"review", "implement"}}:
        record(vector, "P", reachable(VALUES["denied_url"]))
    else:
        record(vector, "A", reachable(VALUES["allowed_url"]))
        record(vector, "N", reachable(VALUES["denied_url"]))
        record(vector, "D", reachable(VALUES["redirect_url"], follow=True))

    record(vector, "L", reachable("http://127.0.0.1:18080/"))
    purl = private_url()
    record(vector, "I", reachable(purl) if purl else None)
    record(vector, "M", reachable("http://169.254.169.254/latest/meta-data/"))
    record(vector, "R", reachable(VALUES["raw_ipv4_url"]))

    if VALUES["ipv6_public_url"] and VALUES["ipv6_raw_url"]:
        record(vector, "U", reachable(VALUES["ipv6_public_url"], ipv6=True))
        record(vector, "V", reachable("http://[::1]:18081/", ipv6=True))
        record(vector, "W", reachable("http://[fd00::1]:9/", ipv6=True))
        record(vector, "X", reachable("http://[fe80::1]:9/", ipv6=True))
        record(vector, "Y", reachable(VALUES["ipv6_raw_url"], ipv6=True))

    defaults_allowed = reachable(VALUES["defaults_url"])
    record(vector, "Z", defaults_allowed)
    defaults_line = DEFAULTS_PREFIX + ("1" if defaults_allowed else "0")
    observation_line = OBS_PREFIX + ";".join(vector) + ";"
    Path({TASK_OBSERVATION_HANDOFF!r}).write_text(
        defaults_line + "\\n" + observation_line + "\\n",
        encoding="utf-8",
    )
    print(defaults_line)
    print(observation_line)

if __name__ == "__main__":
    main()
"""



def outer_wrapper(*, ipv6_enabled: bool) -> str:
    ipv6_setup = ""
    ipv6_control = ""
    if ipv6_enabled:
        ipv6_setup = r"""
python3 -m http.server 18081 --bind ::1 --directory /tmp >/tmp/sec007-http6.log 2>&1 &
server6=$!
"""
        ipv6_control = r"""
if ! curl -6 --noproxy '*' -fsS --connect-timeout 3 --max-time 5 \
    'http://[::1]:18081/' >/dev/null; then
    echo "SEC007 local-control IPv6 loopback HTTP reachability failed" >&2
    exit 74
fi
"""

    return f"""set -u
server4=
server6=
cleanup() {{
    [ -z "$server4" ] || kill "$server4" 2>/dev/null || true
    [ -z "$server6" ] || kill "$server6" 2>/dev/null || true
    rm -f {shlex.quote(PRIVATE_IP_HANDOFF)} {shlex.quote(TASK_OBSERVATION_HANDOFF)}
}}
trap cleanup EXIT INT TERM
python3 -m http.server 18080 --bind 0.0.0.0 --directory /tmp >/tmp/sec007-http4.log 2>&1 &
server4=$!
{ipv6_setup}
sleep 0.5
private_ip=$(hostname -I | tr ' ' '\\n' | awk '/^[0-9]+\\./ && $0 !~ /^127\\./ {{print; exit}}')
case "$private_ip" in
    '') echo "SEC007 local-control private IPv4 resolution failed" >&2; exit 71 ;;
esac
if ! curl --noproxy '*' -fsS --connect-timeout 3 --max-time 5 \
    'http://127.0.0.1:18080/' >/dev/null; then
    echo "SEC007 local-control loopback HTTP reachability failed" >&2
    exit 72
fi
if ! curl --noproxy '*' -fsS --connect-timeout 3 --max-time 5 \
    "http://$private_ip:18080/" >/dev/null; then
    echo "SEC007 local-control private IPv4 HTTP reachability failed" >&2
    exit 73
fi
printf '%s\\n' "$private_ip" > {shlex.quote(PRIVATE_IP_HANDOFF)}
chmod 0444 {shlex.quote(PRIVATE_IP_HANDOFF)}
{ipv6_control}
echo {LOCAL_CONTROL_PASS}
rc=0
"$@" || rc=$?
if [ -f {shlex.quote(TASK_OBSERVATION_HANDOFF)} ]; then
    cat {shlex.quote(TASK_OBSERVATION_HANDOFF)}
else
    echo "SEC007_TASK_OBSERVATION_HANDOFF_MISSING"
fi
exit "$rc"
"""


def parse_observations(text: str) -> dict[str, EgressProbeObservation]:
    payload: str | None = None
    for line in text.splitlines():
        marker = line.find(OBS_PREFIX)
        if marker >= 0:
            payload = line[marker + len(OBS_PREFIX):].strip()

    if payload is None:
        return {}

    observations: dict[str, EgressProbeObservation] = {}
    for token in payload.split(";"):
        if len(token) != 2:
            continue
        key, state = token
        probe_id = COMPACT_PROBE_KEYS.get(key)
        if probe_id is None or probe_id in observations:
            continue
        if state == "1":
            succeeded: bool | None = True
        elif state == "0":
            succeeded = False
        elif state == "e":
            succeeded = None
        else:
            continue
        observations[probe_id] = EgressProbeObservation(
            succeeded,
            "compact Cursor task-egress observation",
        )
    return observations



def add_ipv6_unsupported(
    profile: str,
    observations: dict[str, EgressProbeObservation],
    reason: str | None,
) -> None:
    if reason is None:
        return
    for expectation in task_egress_contract(profile).expectations:
        spec = PROBE_SPEC_BY_ID[expectation.probe_id]
        if spec.address_family == ADDRESS_IPV6:
            observations[expectation.probe_id] = EgressProbeObservation(None, reason)


def current_sandbox_config(control_base: list[str]) -> str:
    script = (
        "import json,pathlib; "
        "p=pathlib.Path('/home/node/.cursor/cli-config.json'); "
        "d=json.loads(p.read_text()) if p.is_file() else {}; "
        "print(json.dumps(d.get('sandbox', {}), sort_keys=True))"
    )
    result = run([*control_base, "python3", "-c", script], capture=True, timeout=30)
    if result.returncode != 0:
        return "<unreadable>"
    return (result.stdout or "").strip() or "{}"



def run_profile(
    cfg: dict,
    workspace: Path,
    policy_file: Path,
    *,
    profile: str,
    phase: str,
    ipv6_enabled: bool,
    ipv6_unsupported_reason: str | None,
) -> bool:
    base = podman_base(
        cfg,
        workspace,
        runtime_isolation=CURSOR_SANDBOX_ISOLATION,
        sandbox_policy=policy_file,
    )
    prompt = (
        "Use the shell tool exactly once to run exactly the command on the next line:\\n"
        f"python3 /workspace/{TASK_SCRIPT} {profile}\\n"
        "Do not retry the command and do not run any other shell command. "
        "After the command attempt, reply done."
    )
    result = run(
        [
            *base,
            "bash",
            "-lc",
            outer_wrapper(ipv6_enabled=ipv6_enabled),
            "sec007-wrapper",
            *cursor_headless_argv(prompt),
        ],
        capture=True,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    task_observation = combined
    observations = parse_observations(task_observation)
    provider_control_ok = (
        result.returncode == 0
        and LOCAL_CONTROL_PASS in combined
    )
    observations[PROBE_PROVIDER_CONTROL_CONNECTIVITY] = EgressProbeObservation(
        provider_control_ok,
        (
            "authenticated Cursor control execution completed"
            if provider_control_ok
            else f"Cursor/control execution returned {result.returncode}"
        ),
    )
    add_ipv6_unsupported(profile, observations, ipv6_unsupported_reason)

    defaults = observations.pop(PROBE_CURSOR_DEFAULTS_BYPASS, None)
    if defaults is None:
        diagnostic = " | ".join(
            line.strip() for line in task_observation.splitlines() if line.strip()
        )
        if diagnostic:
            print(f"SEC007 T6 {profile.upper()} TASK OBSERVATION " + diagnostic)
        print(
            f"SEC007 T6 FAIL: {phase} {profile} did not report Cursor-default-domain observation"
        )
        return False
    if defaults.succeeded is not False:
        print(
            f"SEC007 T6 FAIL: {phase} {profile} reached the Cursor built-in-default fixture; "
            "sandbox.json-only network mode is not enforced"
        )
        if phase == "baseline":
            print(
                "SEC007 T6 CHARACTERIZATION: baseline policy leaked a Cursor-default "
                "destination despite user_config_only; inspect effective global policy "
                "loading and CLI network-mode application"
            )
        else:
            print(
                "SEC007 T6 CHARACTERIZATION: baseline passed but repository sandbox "
                "policy widened the effective Cursor policy; prepare a broker-owned "
                "project-policy guard"
            )
        return False

    report = evaluate_task_egress_contract(
        task_egress_contract(profile),
        ObservationAdapter(observations),
    )
    if report.passed:
        print(f"SEC007 T6 {phase.upper()} {profile.upper()} COMMON EGRESS CONTRACT PASS")
        print(f"SEC007 T6 {phase.upper()} CURSOR DEFAULT-DOMAIN BYPASS DENIED")
        print("SEC007 PROVIDER CONTROL CONNECTIVITY PASS")
        return True

    print(f"SEC007 T6 FAIL: {phase} {profile} common egress contract failed")
    for failure in report.failures:
        print(
            "  "
            f"{failure.probe_id}: expected_allowed={failure.expected_allowed} "
            f"observed={failure.observed_succeeded!r} "
            f"unsupported={failure.unsupported} detail={failure.detail}"
        )
    detail = redacted_detail(combined)
    if detail:
        print(f"  cursor diagnostic: {detail}")
    return False


def main() -> int:
    if os.environ.get(RUN_ENV) != "1":
        print(f"SEC007 SKIP: set {RUN_ENV}=1 for deployed authenticated T5/T6")
        return 0

    config_path = Path(os.environ.get(CONFIG_ENV, str(DEFAULT_CONFIG)))
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    image = cfg["images"]["cursor"]
    if run(["podman", "image", "exists", image]).returncode != 0:
        raise SystemExit(f"missing Cursor image: {image}")

    allowed_url, allowed_host = require_url(ALLOWED_URL_ENV, require_hostname=True)
    denied_url, denied_host = require_url(DENIED_URL_ENV, require_hostname=True)
    redirect_url, redirect_host = require_url(REDIRECT_URL_ENV, require_hostname=True)
    raw_ipv4_url, _ = require_url(RAW_IP_URL_ENV, raw_ip_version=4)
    defaults_url, defaults_host = require_url(DEFAULTS_URL_ENV, require_hostname=True)
    ipv6_public_url, ipv6_raw_url, ipv6_unsupported_reason = ipv6_fixtures()
    ipv6_enabled = ipv6_public_url is not None and ipv6_raw_url is not None

    allowlist_hosts = tuple(sorted({allowed_host, redirect_host}))
    if denied_host in allowlist_hosts:
        raise SystemExit("denied host must not be in the dependency allowlist")
    if defaults_host in allowlist_hosts:
        raise SystemExit("Cursor-default fixture must not be in the dependency allowlist")

    state, auth = cursor_state_mounts()
    for mount in (state, auth):
        if run(["podman", "volume", "exists", mount.source]).returncode != 0:
            raise SystemExit(f"missing Cursor provider volume: {mount.source}")

    with tempfile.TemporaryDirectory(prefix="agentdev-sec007-") as td:
        temp = Path(td)
        workspace = temp / "workspace"
        workspace.mkdir(mode=0o777)
        os.chmod(workspace, 0o777)
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)

        control_base = podman_base(
            cfg,
            workspace,
            runtime_isolation=CURSOR_CONTROL_ISOLATION,
        )

        version = run([*control_base, "agent", "--version"], capture=True, timeout=30)
        version_text = redacted_detail((version.stdout or "") + (version.stderr or ""))
        if version.returncode != 0 or not version_text:
            print("SEC007 T5 FAIL: Cursor version probe failed")
            return 2
        print(f"SEC007 T5 CURSOR VERSION {version_text}")

        expected_version = os.environ.get(
            EXPECTED_VERSION_ENV,
            EXPECTED_CURSOR_VERSION,
        ).strip()
        if expected_version and expected_version not in version_text:
            print(
                "SEC007 T5 FAIL: Cursor version does not match the SEC-007 "
                f"acceptance baseline {expected_version!r}"
            )
            return 2

        status = run([*control_base, "agent", "status"], capture=True, timeout=30)
        if status.returncode != 0:
            print(f"SEC007 T5 FAIL: agent status returned {status.returncode}")
            return 2
        print("SEC007 T5 LOGIN STATUS PASS")
        sandbox_config_text = current_sandbox_config(control_base)
        print("SEC007 T5 CLI SANDBOX CONFIG " + sandbox_config_text)
        try:
            sandbox_config = json.loads(sandbox_config_text)
        except json.JSONDecodeError:
            print("SEC007 T5 FAIL: trusted Cursor sandbox config is unreadable")
            return 2
        observed_network_access = sandbox_config.get("networkAccess")
        if observed_network_access != EXPECTED_NETWORK_ACCESS:
            print(
                "SEC007 T5 FAIL: Cursor sandbox networkAccess is "
                f"{observed_network_access!r}; expected {EXPECTED_NETWORK_ACCESS!r}"
            )
            return 2
        print("SEC007 T5 CLI SANDBOX NETWORK MODE PASS")

        for label, url in (
            ("allowed endpoint", allowed_url),
            ("denied endpoint", denied_url),
            ("raw IPv4 endpoint", raw_ipv4_url),
            ("Cursor built-in-default endpoint", defaults_url),
        ):
            if not trusted_url_control(control_base, label, url):
                return 3
        if not trusted_redirect_control(control_base, redirect_url, denied_host):
            return 3

        if ipv6_enabled:
            assert ipv6_public_url is not None
            assert ipv6_raw_url is not None
            if not trusted_url_control(
                control_base,
                "IPv6 denied endpoint",
                ipv6_public_url,
                ipv6=True,
            ):
                return 3
            if not trusted_url_control(
                control_base,
                "IPv6 raw-IP endpoint",
                ipv6_raw_url,
                ipv6=True,
            ):
                return 3
        else:
            print(
                "SEC007 T6 IPV6 EXPLICITLY UNSUPPORTED: "
                f"{ipv6_unsupported_reason}"
            )

        (workspace / TASK_SCRIPT).write_text(
            task_probe_source(
                denied_url=denied_url,
                allowed_url=allowed_url,
                raw_ipv4_url=raw_ipv4_url,
                redirect_url=redirect_url,
                defaults_url=defaults_url,
                ipv6_public_url=ipv6_public_url,
                ipv6_raw_url=ipv6_raw_url,
            ),
            encoding="utf-8",
        )
        os.chmod(workspace / TASK_SCRIPT, 0o755)

        review_policy = write_policy_file(
            temp,
            workspace_access="read",
            mode="deny",
        )
        implement_policy = write_policy_file(
            temp,
            workspace_access="write",
            mode="deny",
        )
        dependency_policy = write_policy_file(
            temp,
            workspace_access="write",
            mode="allowlist",
            destinations=allowlist_hosts,
        )

        print("SEC007 T5 AUTHENTICATED CONTROL PASS")

        profiles = (
            ("review", review_policy),
            ("implement", implement_policy),
            ("dependency", dependency_policy),
        )

        print("SEC007 T6 BASELINE POLICY PHASE")
        for profile, policy_file in profiles:
            if not run_profile(
                cfg,
                workspace,
                policy_file,
                profile=profile,
                phase="baseline",
                ipv6_enabled=ipv6_enabled,
                ipv6_unsupported_reason=ipv6_unsupported_reason,
            ):
                return 4

        write_project_policy_widening(workspace, denied_host)
        print("SEC007 T6 PROJECT-WIDENING POLICY PHASE")
        for profile, policy_file in profiles:
            if not run_profile(
                cfg,
                workspace,
                policy_file,
                profile=profile,
                phase="project-widening",
                ipv6_enabled=ipv6_enabled,
                ipv6_unsupported_reason=ipv6_unsupported_reason,
            ):
                return 4
        print("SEC007 T6 PROJECT-SANDBOX WIDENING DENIED")

    caps = CursorDriver().capabilities()
    if "provider_state_protection" not in caps.policy_capabilities:
        raise SystemExit("SEC007 FAIL: prior SEC-003 credential capability is missing")
    if "network_deny" in caps.policy_capabilities:
        raise SystemExit("SEC007 FAIL: network_deny advertised before certification")
    if "network_allowlist" in caps.policy_capabilities:
        raise SystemExit("SEC007 FAIL: network_allowlist advertised before certification")
    if "hardened" in caps.security_classes:
        raise SystemExit("SEC007 FAIL: hardened advertised before SEC008")

    print("SEC007 network capabilities remain evidence-gated pending certification")
    print("SEC007 AUTHENTICATED T5/T6 EGRESS PROOF PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
