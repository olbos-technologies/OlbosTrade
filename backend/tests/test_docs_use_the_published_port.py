r"""
Docs name the port the Hetzner stack actually publishes.

ARCHITECTURE_AUDIT.md recorded the production server as `46.224.0.213:8081`.
The compose file has published `8080:3000` since it was first committed, and the
running server publishes 8080 — so that line was wrong the day it was written,
not merely stale.

It was not harmless. During the 2026-09-17 outage the operator's bookmark
pointed at :8081, so the app looked dead from the browser even once it was
serving; and the wrong port sent the investigation toward "the server's config
has diverged from the repo", which was backwards — the document had diverged
from both.

Worse, no doc named the RIGHT port at all. `grep -rn 8080 --include=*.md` came
back empty. Someone deploying without a domain had nowhere to look but the
compose file's inline comment.

So this asserts a direction, not a value: every literal `IP:port` in the docs
must use a port the Hetzner compose file publishes. Restating "8080" in a test
would only agree with itself; parsing both sides means changing the compose
port makes the docs fail until they follow.

Scope is deliberately narrow — only PUBLIC-IP-literal references. localhost and
127.0.0.1 appear throughout README.md and DEPLOY_V3.md for local development
and for curling the backend from inside its own container; those ports are not
published to the host and must not be dragged into this.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.hetzner.yml"

#: A dotted-quad followed by :port, in any markdown file.
IP_PORT = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\b")

PRIVATE_PREFIXES = ("127.", "0.", "10.", "192.168.", "172.16.", "172.17.", "255.")

def is_loopback_bind(bind: str) -> bool:
    """Does this `ports:` bind address expose the port to the host only?

    Compose accepts more than a dotted quad here — `localhost:8080:3000` and
    `[::1]:8080:3000` are both valid and both loopback. An earlier version of
    this module listed "localhost" in a prefix tuple while the entry regex
    could only ever produce a dotted quad, so that arm was unreachable: it
    read as coverage and was not. Caught by Copilot on #67.
    """
    b = bind.strip().strip("[]").lower()
    return b.startswith("127.") or b == "localhost" or b == "::1"


#: A `ports:` entry, with the OPTIONAL bind address captured separately.
#: Compose accepts "8080:3000" (all interfaces) and "127.0.0.1:8080:3000"
#: (loopback only). Those two mean opposite things for everything below, so
#: the bind address cannot be folded into the port.
#: The bind may be a dotted quad, a hostname, or a bracketed IPv6 literal.
#: Anchored on the host:container pair at the end, so "8080:3000" (no bind)
#: still parses: the engine tries bind="8080", finds no second colon pair
#: after it, and backtracks.
PORT_ENTRY = re.compile(
    r'^\s*-\s*"?(?:(?P<bind>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+):)?'
    r'(?P<host>\d{2,5}):(?P<container>\d{2,5})"?\s*$'
)


def compose_port_entries() -> list[tuple[str, str, int]]:
    """(service, bind address, host port) for every `ports:` entry.

    Bind address is "" when Compose was given none, which means 0.0.0.0 — every
    interface, including the public one.

    The SERVICE is tracked because the two kinds of public port mean opposite
    things. Caddy publishing 80/443 is the deployment working as designed; an
    application container publishing a port is a plain-HTTP route around it.
    An earlier version of this module lumped them together and asserted that
    nothing at all was public, which was true only while no reverse proxy lived
    in this file — and the moment Caddy moved in, the honest fix would have
    looked like weakening the test.

    Comments are stripped: the `ports:` block carries an explanatory comment
    naming the URL, and this repo has already shipped one guard that matched
    its own prose instead of the setting.
    """
    lines = [
        l for l in COMPOSE.read_text().splitlines()
        if not l.lstrip().startswith("#")
    ]
    entries: list[tuple[str, str, int]] = []
    service = ""
    for line in lines:
        svc = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", line)
        if svc:
            service = svc.group(1)
            continue
        m = PORT_ENTRY.match(line)
        if m:
            entries.append((service, m.group("bind") or "", int(m.group("host"))))
    return entries


#: The one service allowed to publish on every interface, and the only ports it
#: may publish. Caddy terminates TLS and answers ACME challenges, so it has to
#: be reachable from the internet — that is its whole job. 80 must stay open
#: too: with no `:80` site block Caddy uses it for certificate challenges and a
#: 308 to HTTPS, which carries no app content and no credentials.
PUBLIC_PROXY_SERVICE = "caddy"
PUBLIC_PROXY_PORTS = {80, 443}


def publicly_published_ports() -> set[int]:
    """Host ports reachable from OFF the machine, whatever publishes them.

    A loopback-bound entry publishes on 127.0.0.1 only. Calling that
    "published" is what would let a doc go on advertising
    `http://<public-ip>:8080` after the bind closed it — the precise drift this
    module exists to catch, just pointing the other way.
    """
    return {port for _svc, bind, port in compose_port_entries()
            if not is_loopback_bind(bind)}


def publicly_published_app_ports() -> set[tuple[str, int]]:
    """(service, port) for public ports that are NOT the reverse proxy's.

    This is the set that must stay empty. Everything in this stack is reached
    either through Caddy or from on the host; anything else publishing to the
    world is a plain-HTTP way around the TLS.
    """
    return {
        (svc, port) for svc, bind, port in compose_port_entries()
        if not is_loopback_bind(bind)
        and not (svc == PUBLIC_PROXY_SERVICE and port in PUBLIC_PROXY_PORTS)
    }


def loopback_only_ports() -> set[int]:
    """Host ports bound to 127.x — reachable from on the host, e.g. via an SSH
    tunnel, and from nowhere else."""
    return {port for _svc, bind, port in compose_port_entries()
            if is_loopback_bind(bind)}


def markdown_files() -> list[Path]:
    return [
        p for p in REPO.rglob("*.md")
        if "node_modules" not in p.parts and ".git" not in p.parts
    ]


def documented_ip_ports() -> list[tuple[Path, int, int, str]]:
    """(file, line_no, port, the line) for each public-IP:port in the docs."""
    found = []
    for path in markdown_files():
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for ip, port in IP_PORT.findall(line):
                if ip.startswith(PRIVATE_PREFIXES):
                    continue
                found.append((path, n, int(port), line.strip()))
    return found


def test_the_port_entry_parser_handles_every_bind_form_compose_accepts():
    """Compose's bind address is not always a dotted quad.

    Written because the first version of PORT_ENTRY matched dotted quads only
    while is_loopback_bind's predecessor also listed "localhost" — an arm the
    regex could never reach. A `localhost:8080:3000` entry would have failed
    to parse at all, and with another entry present to keep
    compose_port_entries() non-empty, guard-the-guard would not have fired:
    the port would simply vanish from both the public and loopback sets, so
    nothing would require a tunnel for it and nothing would flag it as public.

    Asserting on the parser directly rather than only through the compose file
    is deliberate: the file holds one entry today, so every branch here is
    otherwise untested.
    """
    cases = [
        ('      - "8080:3000"',                 "",            8080, False),
        ('      - "127.0.0.1:8080:3000"',       "127.0.0.1",   8080, True),
        ('      - "127.1.2.3:9000:3000"',       "127.1.2.3",   9000, True),
        ('      - "localhost:8080:3000"',       "localhost",   8080, True),
        ('      - "[::1]:8080:3000"',           "[::1]",       8080, True),
        ('      - "0.0.0.0:8080:3000"',         "0.0.0.0",     8080, False),
        ('      - "10.0.0.5:8080:3000"',        "10.0.0.5",    8080, False),
        ('      - 8080:3000',                   "",            8080, False),
    ]
    for line, want_bind, want_port, want_loopback in cases:
        m = PORT_ENTRY.match(line)
        assert m is not None, f"PORT_ENTRY did not match a valid entry: {line!r}"
        assert (m.group("bind") or "") == want_bind, (
            f"{line!r}: bind parsed as {m.group('bind')!r}, expected {want_bind!r}"
        )
        assert int(m.group("host")) == want_port, (
            f"{line!r}: host port parsed as {m.group('host')!r}"
        )
        assert is_loopback_bind(m.group("bind") or "") is want_loopback, (
            f"{line!r}: is_loopback_bind({(m.group('bind') or '')!r}) should be "
            f"{want_loopback}. Misclassifying here is silent: the port drops out "
            f"of both the public and the loopback set, so nothing requires a "
            f"tunnel for it and nothing flags it as exposed."
        )


def test_the_compose_ports_were_actually_parsed():
    """Guards the guard: if the `ports:` shape changes and the regex stops
    matching, every set below silently empties and the membership checks pass
    vacuously. Asserting on ENTRIES rather than on public ports is deliberate —
    binding everything to loopback is a legitimate end state that empties
    publicly_published_ports() honestly, and this assertion must not fire on
    it."""
    entries = compose_port_entries()
    assert entries, (
        "no `ports:` entries parsed from docker-compose.hetzner.yml. Either the "
        "stack stopped publishing anything at all, or the entry shape changed "
        "and this test can no longer see it. If a bind address was added in a "
        "form PORT_ENTRY does not match, fix the regex — do not delete this."
    )


def test_every_documented_ip_port_is_one_the_stack_publishes():
    """A public `IP:port` in the docs must be reachable at that address.

    Loopback-bound ports do not count. `127.0.0.1:8080:3000` answers only from
    on the host, so a doc line naming `<public-ip>:8080` sends a reader to a
    connection that hangs — the same failure as the original `:8081`, reached
    from the other direction: the port is right and the reachability is not.
    """
    published = publicly_published_ports()
    loopback = loopback_only_ports()
    wrong = [
        (str(p.relative_to(REPO)), n, port, line)
        for p, n, port, line in documented_ip_ports()
        if port not in published
    ]
    assert not wrong, (
        "these docs name a public IP:port the Hetzner stack does not serve "
        f"there (publicly published: {sorted(published)}; loopback-only: "
        f"{sorted(loopback)}):\n"
        + "\n".join(f"  {f}:{n} → :{port}\n      {line}" for f, n, port, line in wrong)
        + "\n\nIf the port is in the loopback-only list, the fix is to stop "
          "advertising it at a public address — point the line at the HTTPS "
          "domain, or document the SSH tunnel. A wrong URL here reads as a dead "
          "app: on 2026-09-17 it sent an outage investigation toward the "
          "server's config when the document was what had drifted."
    )


def test_no_application_port_is_published_to_the_public_interface():
    """Only the reverse proxy may publish publicly, and only on 80/443.

    The scope is every service in the compose file, not just the frontend.
    Copilot caught the docstring claiming the narrower invariant on #67, which
    matters because the next person to add a `ports:` entry would read the
    docstring, assume only the frontend was constrained, and be surprised by
    the failure — or worse, narrow the assertion to match the prose and quietly
    reopen the stack to public binds.

    The proxy exemption is NOT that weakening arriving by the back door. When
    Caddy moved into this file on 2026-09-19 the flat "nothing is public" rule
    became false by design — a TLS terminator must be reachable — and the
    choice was between an honest exemption naming exactly one service and two
    ports, or deleting the test. The exemption is checked: a public port on any
    other service, or on Caddy at any other port, still fails.

    This is an invariant, not a preference. Caddy reaches the frontend over
    docker_default, so a public bind adds no capability — it only adds a
    plain-HTTP route into the same app. Two things make that route worse than
    it looks: HTTP Basic sends DASH_USER/DASH_PASS base64-encoded (reversible
    by anyone reading the traffic), and the Trade Desk's own 403 message asks
    the operator to paste SECRET_KEY on the Risk Monitor page.

    Written because mutation testing found the gap: reverting compose to
    "8080:3000" passed every other test in this file, while deploy README's
    step 7b went on asserting the bind was loopback. Prose claiming a config
    value with nothing checking it is the exact defect this module exists for,
    reproduced by the module's own author.

    `ufw deny 8080` is NOT an acceptable substitute and does not satisfy this
    test, deliberately: Docker's DNAT and FORWARD rules run before UFW's, so a
    published port stays reachable whatever `ufw status` reports.

    If a port genuinely must be public one day, change this test in the same
    commit that changes compose, and say in the message why the exposure is
    acceptable. Do not delete it to make a deploy go through.
    """
    public = publicly_published_app_ports()
    assert not public, (
        "docker-compose.hetzner.yml publishes these on ALL interfaces:\n"
        + "\n".join(f"  service {svc!r} → :{port}" for svc, port in sorted(public))
        + f"\n\nOnly {PUBLIC_PROXY_SERVICE!r} may do that, and only on "
          f"{sorted(PUBLIC_PROXY_PORTS)}. Bind the rest to loopback — "
          f"`127.0.0.1:<port>:<container>` — so they answer only from on the "
          f"host. An SSH tunnel (`ssh -L <port>:localhost:<port> root@<server>`) "
          f"still reaches them, and Caddy reaches the containers over "
          f"docker_default rather than through any host port."
    )


#: `<SOME_PLACEHOLDER>:port`, the form a deploy guide uses for an address the
#: reader substitutes. IP_PORT above only matches literal dotted quads, so
#: every one of these was invisible to it.
PLACEHOLDER_HOST_PORT = re.compile(r"<[A-Za-z0-9_-]+>:(\d{2,5})\b")


def readme_prose_placeholder_ports() -> list[tuple[int, int, str]]:
    """(line_no, port, line) for `<PLACEHOLDER>:port` outside fenced code.

    Prose only, and the exclusion is the whole design. Step 7b's verification
    command MUST name the address — its job is to prove the port is shut, and
    it reports success by failing to connect. Prose is where "go here to reach
    the app" lives, which is the claim that goes false when a bind changes.

    The residual gap is real and stated rather than papered over: an
    instruction hidden inside a ``` block would not be caught. Closing that
    would mean flagging the one command that legitimately names the address,
    and a guard that cries wolf on its own verification step gets deleted.
    """
    found: list[tuple[int, int, str]] = []
    in_fence = False
    for n, line in enumerate(DEPLOY_README.read_text().splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for port in PLACEHOLDER_HOST_PORT.findall(line):
            found.append((n, int(port), line.strip()))
    return found


def test_the_readme_prose_does_not_advertise_a_loopback_port_publicly():
    """Prose must not send a reader to `<server-ip>:PORT` for a loopback port.

    Sourcery caught this on #67 and it is the third time in this run of PRs
    that I have fixed a defect in one place and left it standing in another.
    Step 7b was rewritten to say the bind is loopback while step 7 went on
    telling an operator without a domain to open `http://<YOUR_HETZNER_IP>:8080`
    — which, after the bind, is a connection that hangs.

    It escaped every existing check because IP_PORT matches dotted quads only,
    and a deploy guide naturally writes the address as a placeholder. So the
    guard that exists to catch "the docs name an address that does not answer"
    was blind to the entire form the docs actually use.
    """
    loopback = loopback_only_ports()
    wrong = [
        (n, port, line) for n, port, line in readme_prose_placeholder_ports()
        if port in loopback
    ]
    assert not wrong, (
        f"deploy/hetzner/README.md prose points a reader at a placeholder host "
        f"address on port(s) {sorted(loopback)}, which compose binds to "
        f"loopback — so that address does not answer from anywhere but the "
        f"server itself:\n"
        + "\n".join(f"  line {n} → :{port}\n      {line}" for n, port, line in wrong)
        + "\n\nPoint it at the HTTPS domain, or at the SSH tunnel "
          "(`ssh -L <port>:localhost:<port> root@<server>`, then "
          "http://localhost:<port>)."
    )


def test_the_readme_tells_an_operator_how_to_reach_the_app():
    """The original gap was not "the port was wrong" — it was that NO doc named
    a working way in, so a wrong one went unchallenged for months.

    What counts as a working way in depends on what compose publishes, so this
    asserts the direction rather than a fixed answer. Two routes must be
    documented, because they fail independently:

      * PRIMARY — the HTTPS domain Caddy serves. Always required: the proxy is
        how this app is meant to be reached.
      * FALLBACK — an SSH tunnel for every loopback-bound port. Required
        precisely BECAUSE the primary can fail: a bad Caddyfile, an expired
        certificate or a DNS problem leaves the domain dead, and the loopback
        ports are then the only way in. Documenting only the primary is what
        turns a TLS problem into a lockout.

    The old version had an `if published: ... return` branch that named a
    public app port instead. That branch is gone with the port it described —
    keeping it would have meant Caddy's own 80/443 satisfying "the README names
    a published port", which is true and useless: nobody reaches this app by
    typing a port number any more.
    """
    loopback = loopback_only_ports()
    readme = DEPLOY_README.read_text()
    domain = caddy_site_domain()

    assert domain and re.search(r"https://" + re.escape(domain), readme), (
        f"deploy/hetzner/README.md never names https://{domain} — the URL Caddy "
        f"actually serves and the primary way into this deployment."
    )

    assert loopback, (
        "compose publishes nothing on loopback, so there is no direct route in "
        "if Caddy is broken. If that is genuinely intended, rewrite this test "
        "deliberately rather than deleting it — and say in the commit how an "
        "operator is meant to reach a stack whose TLS has failed."
    )
    for port in sorted(loopback):
        assert re.search(rf"ssh\s+-L\s+{port}:localhost:{port}\b", readme), (
            f"port {port} is bound to loopback, so an SSH tunnel is the only "
            f"direct route to it — and deploy/hetzner/README.md does not show "
            f"one (`ssh -L {port}:localhost:{port} ...`). Closing a public port "
            f"without documenting the replacement leaves an operator locked out "
            f"during an incident, which is worse than the exposure it fixed."
        )


# ── The domain half of the same problem ──────────────────────────────────────
#
# The port drift (`:8081` in the audit doc against `8080:3000` in compose) had a
# twin waiting: the Caddy site block and the README's "open this URL" line live
# in different files with nothing tying them together. Rename one and the other
# sends an operator to a host that answers nothing — during an incident, which
# is the only time anyone reads a deploy guide.

#: The real, mounted Caddy config — not a snippet to paste any more. Caddy
#: moved into this repo's compose file on 2026-09-19, so the config Caddy
#: actually loads is now a tracked file and this guard reads THAT rather than
#: an instruction to copy it somewhere.
CADDYFILE = REPO / "deploy" / "hetzner" / "Caddyfile"
DEPLOY_README = REPO / "deploy" / "hetzner" / "README.md"


def caddy_site_domain() -> str:
    """The hostname Caddy actually serves, from its site block.

    On parsing: the file's header comment names the domain in prose, so a
    loose scan would match the explanation instead of the directive.

    What prevents that is the ANCHORED regex — the whole line must be
    `<domain> {` — not the comment stripping. Mutation testing: delete the
    site block, keep the comment, disable the stripping, and this still
    returns "" correctly.

    Worth stating plainly because I claimed the opposite here, and twice
    before in this file's sibling guards, each time writing the justification
    before testing it. The stripping stays as defence in depth against a
    future comment that is itself a bare `something.tld {` line; it is not
    what is holding today. Loosening the regex is what would break this.
    """
    lines = [l for l in CADDYFILE.read_text().splitlines()
             if not l.lstrip().startswith("#")]
    for line in lines:
        m = re.match(r"^\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*\{\s*$", line)
        if m:
            return m.group(1)
    return ""


def test_the_caddy_site_block_was_found():
    """Guards the guard: an empty domain makes the comparison vacuous."""
    assert caddy_site_domain(), (
        "no site block parsed out of deploy/hetzner/Caddyfile. Either it "
        "changed shape or it no longer declares a site — the comparison below "
        "would otherwise pass against nothing. Note this is the config Caddy "
        "actually mounts, so an empty result here means the deployment serves "
        "nothing, not merely that a doc drifted."
    )


#: Hosts the README may legitimately reference that Caddy does not serve.
EXTERNAL_HOSTS = {"github.com"}


def readme_https_hosts() -> set[str]:
    """Every host the README tells an operator to reach over HTTPS."""
    return {
        h for h in re.findall(r"https://([A-Za-z0-9.-]+)", DEPLOY_README.read_text())
        if h not in EXTERNAL_HOSTS
    }


def test_the_deploy_readme_names_the_domain_caddy_serves():
    domain = caddy_site_domain()
    assert domain in readme_https_hosts(), (
        f"deploy/hetzner/Caddyfile serves {domain!r} but no https:// URL in "
        f"deploy/hetzner/README.md points there. The README is what an "
        f"operator follows; if it names a different host they will curl "
        f"something that does not answer and conclude the deploy failed when "
        f"it did not."
    )


def test_every_https_url_in_the_readme_points_at_that_domain():
    r"""Not just "the domain appears somewhere".

    The first version asserted `domain in readme`, which passes while the
    actual `Open https://...` line names a different host — the domain still
    occurs in the architecture diagram and the DNS row. That is the same
    "appears anywhere" weakness as the bare \b(\d{4,5})\b scan two tests up,
    and it would have missed precisely the drift this guard exists for.
    Raised in review on PR #66.

    Checking every URL rather than one specific line also survives rewording:
    a guard pinned to exact prose fails on an edit that changed nothing real.
    """
    domain = caddy_site_domain()
    wrong = readme_https_hosts() - {domain}
    assert not wrong, (
        f"deploy/hetzner/README.md sends operators to {sorted(wrong)} over "
        f"HTTPS, but Caddy only serves {domain!r}. Those URLs answer nothing. "
        f"If one of them is a genuine external link, add it to EXTERNAL_HOSTS."
    )


def test_no_placeholder_domain_survives_the_rename():
    """A half-finished rename is worse than either state on its own."""
    # deploy/ plus the env templates at the repo root. A placeholder hostname
    # in .env.example misleads exactly as much as one in the README, and the
    # original scan stopped at deploy/hetzner.
    targets = list((REPO / "deploy").rglob("*"))
    targets += [REPO / ".env.example", REPO / ".env.prod.example"]

    stale = []
    for path in targets:
        if not path.is_file() or path.suffix in {".sql", ".png"}:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if "yourdomain" in line:
                stale.append(f"{path.relative_to(REPO)}:{n}")
    assert not stale, (
        f"placeholder domains still present after the rename: {stale}. Mixed "
        f"real and placeholder hostnames in one deploy guide is the worst of "
        f"both — a reader cannot tell which lines they are meant to edit."
    )


# ── The container-name half of the same problem ──────────────────────────────
#
# Caddy moved into this repo's compose file on 2026-09-19 and was renamed
# olbos-caddy -> olbostrade-caddy in the same change, because the old name
# pointed at a project that had been deleted. Every `docker exec <name> caddy
# reload` in the docs had to move with it, and a stale one fails with
# "No such container" at the exact moment someone is trying to fix TLS.
#
# Same shape as the port and domain guards above: the authoritative value lives
# in compose, the copies live in prose, and nothing tied them together.

CADDY_EXEC = re.compile(r"docker\s+exec\s+(?:-\w+\s+)*([A-Za-z0-9_.-]+)\s+caddy\b")


def compose_caddy_container() -> str:
    """The container_name of the `caddy` service, from compose."""
    lines = COMPOSE.read_text().splitlines()
    in_caddy = False
    for line in lines:
        svc = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", line)
        if svc:
            in_caddy = svc.group(1) == "caddy"
            continue
        if in_caddy:
            m = re.match(r"^\s+container_name:\s*(\S+)\s*$", line)
            if m:
                return m.group(1)
    return ""


def deploy_files_naming_a_caddy_container() -> list[tuple[str, int, str]]:
    """(file, line_no, container) for every `docker exec <x> caddy ...`."""
    found: list[tuple[str, int, str]] = []
    targets = [DEPLOY_README, CADDYFILE, *sorted((REPO / "deploy").rglob("*.sh"))]
    for path in targets:
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for name in CADDY_EXEC.findall(line):
                found.append((str(path.relative_to(REPO)), n, name))
    return found


def test_the_compose_caddy_container_name_was_found():
    """Guards the guard: an empty name would make the check below vacuous —
    every reference would compare against "" and the list of mismatches would
    be everything, or nothing, depending on the direction of the test. Assert
    it directly instead."""
    assert compose_caddy_container(), (
        "no container_name found for the `caddy` service in "
        "docker-compose.hetzner.yml. Either the service was removed (the app "
        "has no TLS) or the file's shape changed and this test stopped seeing "
        "it."
    )


def test_every_documented_caddy_exec_names_the_container_compose_declares():
    expected = compose_caddy_container()
    wrong = [
        (f, n, name) for f, n, name in deploy_files_naming_a_caddy_container()
        if name != expected
    ]
    assert not wrong, (
        f"these docs run `docker exec` against a container that is not the one "
        f"compose declares ({expected!r}):\n"
        + "\n".join(f"  {f}:{n} → {name!r}" for f, n, name in wrong)
        + "\n\nA stale name fails with 'No such container' at the moment "
          "someone is trying to reload TLS during an incident. olbos-caddy was "
          "the previous owner's name and no longer exists."
    )


def test_at_least_one_doc_shows_how_to_reload_caddy():
    """Otherwise the guard above is satisfied by naming it nowhere at all.

    Reloading is the operation an operator needs under pressure — a config
    change that does not take effect reads as a broken deploy — so the guide
    has to show it, not merely avoid getting it wrong.
    """
    refs = deploy_files_naming_a_caddy_container()
    assert refs, (
        "no deploy doc or script shows `docker exec <caddy> caddy reload`. The "
        "name-consistency check above passes vacuously when nothing names the "
        "container, and an operator has nowhere to learn how to apply a "
        "Caddyfile change without restarting the container."
    )
