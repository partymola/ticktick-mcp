"""Tests for the container's storage contract.

Nothing here runs the image. These are source-reading assertions about a seam
pytest cannot otherwise reach: the container's config directory is set in the
Dockerfile, mounted by the runtime argument in `server.json`, and described in
the README, including its Configuration table. All four have to name the same
directory, and a change to one of them is invisible to every behavioural test.

What these do NOT check: that the running process receives the environment. An
ENTRYPOINT that scrubbed it would pass here and fail in reality.
"""

import json
import re
import unittest
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
SERVER_JSON = REPO_ROOT / "server.json"
README = REPO_ROOT / "README.md"

MOUNT_POINT = "/data"


def _final_stage_instructions(text=None):
    """Yield (instruction, argument) for the final build stage only.

    This image is multi-stage. An ENV in the build stage does not ship, so
    flattening the whole file would accept a Dockerfile that sets nothing in
    the stage that actually runs. `text` is for exercising this rule directly.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", DOCKERFILE.read_text() if text is None else text)
    parsed = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        instruction, _, argument = line.partition(" ")
        parsed.append((instruction.upper(), argument.strip()))

    last_from = max((i for i, (k, _) in enumerate(parsed) if k == "FROM"), default=-1)
    return parsed[last_from + 1 :]


def _dockerfile_env(text=None):
    """Both ENV forms: `ENV k=v [k2=v2 ...]` and the legacy `ENV k v`."""
    env = {}
    for instruction, argument in _final_stage_instructions(text):
        if instruction != "ENV":
            continue
        if "=" in argument:
            for pair in argument.split():
                key, sep, value = pair.partition("=")
                if sep:
                    env[key] = value
        else:
            key, _, value = argument.partition(" ")
            if value:
                env[key] = value.strip()
    return env


def _under(path, directory):
    """PurePosixPath rather than os.path: these are paths inside a Linux image
    but the comparison runs on whatever host the suite is on, and the native
    flavour on Windows rewrites them with backslashes so nothing contains
    anything."""
    path = PurePosixPath(path)
    directory = PurePosixPath(directory)
    return path == directory or directory in path.parents


def _readme_code_lines():
    """Lines inside fenced code blocks, with continuations joined.

    Scoped to code blocks so prose may mention a command without these
    assertions treating it as one a user is told to run.
    """
    lines, fenced = [], False
    for line in README.read_text().splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            lines.append(line)
    return re.sub(r"\\\s*\n\s*", " ", "\n".join(lines)).splitlines()


class TestTheContainerKeepsItsTokensOnAVolume(unittest.TestCase):
    def test_the_final_stage_moves_the_config_dir_off_the_home_default(self):
        # The default resolves under the container's own HOME, which nothing is
        # told to mount, so every start would re-run a throttled signon.
        self.assertEqual(_dockerfile_env().get("TICKTICK_MCP_DOTENV_DIR"), MOUNT_POINT)

    def test_the_config_dir_sits_under_the_declared_volume(self):
        volumes = set()
        for instruction, argument in _final_stage_instructions():
            if instruction != "VOLUME":
                continue
            try:
                parsed = json.loads(argument)
            except json.JSONDecodeError:
                parsed = [argument]
            volumes.update(parsed if isinstance(parsed, list) else [parsed])
        self.assertIn(MOUNT_POINT, volumes)

        configured = _dockerfile_env().get("TICKTICK_MCP_DOTENV_DIR")
        self.assertTrue(
            any(_under(configured, v) for v in volumes),
            f"{configured} is not under any declared VOLUME {sorted(volumes)}",
        )

    def test_the_registry_entry_mounts_the_same_directory(self):
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        mounts = [
            argument["value"]
            for argument in package.get("runtimeArguments", [])
            if argument.get("name") == "-v"
        ]
        self.assertEqual(len(mounts), 1, "expected exactly one volume runtime argument")
        self.assertEqual(mounts[0].split(":")[1], MOUNT_POINT)

    def test_the_mount_source_is_prompted_for_rather_than_defaulted(self):
        # There is no portable value that works: a bare name is a named volume
        # (created empty, so no cached token), a relative path silently binds
        # somewhere under the client's working directory, and `~` is rejected
        # outright by docker when it is exec'd without a shell to expand it.
        # Only the user knows their own absolute path, so prompt for it - a
        # placeholder shows the shape without shipping an unusable value.
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        mount = next(a for a in package["runtimeArguments"] if a.get("name") == "-v")
        spec = mount["variables"]["data_volume"]

        self.assertNotIn("default", spec, "no default can work here; prompt instead")
        self.assertNotIn("value", spec)
        self.assertTrue(spec.get("isRequired"))
        # PurePosixPath, not os.path: the placeholder is POSIX-shaped, and since
        # 3.13 ntpath.isabs calls a single leading slash relative, so this read
        # as a missing placeholder on a Windows host.
        self.assertTrue(
            PurePosixPath(spec.get("placeholder", ".")).is_absolute(),
            "placeholder must be absolute",
        )

    def test_every_substitution_names_a_variable_that_exists(self):
        # An unmatched `{name}` is left as literal text by the client, and
        # docker then rejects `{name}:/data` as an invalid volume name - a hard
        # failure on every start, from a one-word drift between two lines.
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        for argument in package.get("runtimeArguments", []):
            declared = set(argument.get("variables", {}))
            used = set(re.findall(r"\{([^}]+)\}", argument.get("value", "")))
            self.assertEqual(used, declared, f"{argument['name']}: {used} vs {declared}")

    def test_the_registry_route_drops_root_like_the_readme_does(self):
        # Declared in both places or in neither: a registry client that only
        # gets the mount reproduces the outage this release exists to fix,
        # writing root-owned files a later host-side run cannot update.
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        flags = {a.get("name") for a in package.get("runtimeArguments", [])}
        self.assertEqual(flags, {"--user", "-v"})

    def test_no_runtime_argument_is_optional_or_carries_a_value(self):
        # Parametrized rather than written against one argument: both need the
        # same four properties, and a relaxation on either is enough. Optional
        # means a client may legitimately omit it; a default or value means it
        # ships a guess instead of asking, and no guess works for either flag.
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        arguments = package.get("runtimeArguments", [])
        self.assertTrue(arguments)

        for argument in arguments:
            name = argument["name"]
            self.assertTrue(argument.get("isRequired"), f"{name} is optional")
            self.assertTrue(argument.get("variables"), f"{name} declares no variable")
            for key, spec in argument["variables"].items():
                self.assertTrue(spec.get("isRequired"), f"{name}/{key} is optional")
                self.assertNotIn("default", spec, f"{name}/{key} ships a guess")
                self.assertNotIn("value", spec, f"{name}/{key} ships a guess")
                self.assertTrue(spec.get("placeholder"), f"{name}/{key} shows no example")

    def test_the_runtime_is_named_so_a_client_knows_what_to_exec(self):
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        self.assertEqual(package.get("runtimeHint"), "docker")
        for argument in package.get("runtimeArguments", []):
            # A positional argument would emit the value with no flag at all.
            self.assertEqual(argument.get("type"), "named")

    def test_every_documented_docker_run_carries_what_the_readme_calls_required(self):
        # The README states all three as mandatory, so each is a sentence that
        # needs pinning: the mount, `-i` (the server speaks JSON-RPC over
        # stdin), and `--user` (root-owned files in the mount stop a later
        # host-side run caching its session token).
        runs = [line for line in _readme_code_lines() if "docker run" in line]
        self.assertTrue(runs, "the README documents no docker run")
        for line in runs:
            # Everything docker must consume has to sit before the image name;
            # past it, docker hands the token to the entrypoint instead. `-i`
            # there leaves the server with no stdin, and it hangs.
            before_image = line.split("ghcr.io/")[0]
            self.assertRegex(before_image, rf"-v\s+\S+:{MOUNT_POINT}\b")
            self.assertRegex(before_image, r"(^|\s)-i(\s|$)")
            # The value must not itself be a flag: `--user -v ...` makes docker
            # take `-v` as the user spec and the mount silently disappears.
            self.assertRegex(before_image, r"--user\s+[^-\s]\S*")

    def test_the_configuration_table_names_the_container_path(self):
        # The fourth place that must agree with the Dockerfile, and the one a
        # reader consults when a mount is not working. Matched as the literal
        # backticked path: a substring check also passes on /data/config.
        rows = [
            ln
            for ln in README.read_text().splitlines()
            if "TICKTICK_MCP_DOTENV_DIR" in ln and "|" in ln
        ]
        self.assertTrue(rows, "no Configuration row for the config dir")
        for row in rows:
            self.assertIn(f"`{MOUNT_POINT}`", row)


class TestEveryPieceOfLocalStateSitsOnTheVolume(unittest.TestCase):
    def test_the_completion_database_follows_the_config_dir(self):
        # It is the local state behind the completion-tracking tools, and it
        # is the one piece not held by a credential-permissions test - moved
        # off the config dir it would be lost on every container restart with
        # nothing reporting it.
        from ticktick_mcp import completion_db, config

        self.addCleanup(setattr, completion_db, "_DB_PATH", completion_db._DB_PATH)
        completion_db._DB_PATH = None
        self.assertEqual(
            completion_db._get_db_path().parent,
            config.dotenv_dir_path,
        )


class TestTheParserRulesThemselves(unittest.TestCase):
    """The two rules the Dockerfile assertions rest on.

    Both were reachable only through a correct Dockerfile, so a parser that
    quietly stopped honouring either still passed everything above. These
    drive the parser directly with fixtures instead.
    """

    def test_an_env_above_a_later_from_does_not_count(self):
        # It belongs to the earlier stage and never reaches the image.
        env = _dockerfile_env(
            "FROM base AS build\nENV TICKTICK_MCP_DOTENV_DIR=/data\nFROM base\nWORKDIR /app\n"
        )
        self.assertNotIn("TICKTICK_MCP_DOTENV_DIR", env)
        # Positive control: the same ENV in the final stage is seen.
        env = _dockerfile_env("FROM base AS build\nFROM base\nENV TICKTICK_MCP_DOTENV_DIR=/data\n")
        self.assertEqual(env["TICKTICK_MCP_DOTENV_DIR"], "/data")

    def test_the_legacy_space_form_is_read(self):
        # `ENV k v` is valid and would otherwise be invisible, so a trailing
        # one could redirect the config dir with every assertion still green.
        env = _dockerfile_env(
            "FROM base\nENV TICKTICK_MCP_DOTENV_DIR=/data\n"
            "ENV TICKTICK_MCP_DOTENV_DIR /root/.config/ticktick-mcp\n"
        )
        self.assertEqual(env["TICKTICK_MCP_DOTENV_DIR"], "/root/.config/ticktick-mcp")


class TestNothingOverridesTheConfigDirAtStartup(unittest.TestCase):
    def test_the_entrypoint_starts_the_server_and_passes_no_dotenv_dir(self):
        # Two halves, and the negative one is worthless alone: with no
        # ENTRYPOINT at all the loop body never runs, and the image falls back
        # to the base image's CMD - a Python REPL answering the client on
        # stdout instead of JSON-RPC. --dotenv-dir outranks the environment
        # variable, so an ENTRYPOINT carrying one defeats the ENV silently.
        entrypoints = [a for k, a in _final_stage_instructions() if k == "ENTRYPOINT"]
        self.assertEqual(len(entrypoints), 1, "the final stage must set exactly one ENTRYPOINT")
        self.assertIn("ticktick-mcp", entrypoints[0])

        for instruction, argument in _final_stage_instructions():
            if instruction in ("ENTRYPOINT", "CMD"):
                self.assertNotIn("--dotenv-dir", argument)


class TestTheDocsNameTheImageThatIsActuallyPublished(unittest.TestCase):
    """The workflow is the source of truth for where images land.

    The README tells users to pull a path, `server.json` tells a client to run
    one, and only `publish-registry.yml` decides where either exists. Nothing
    at runtime reads any of the three, so they can drift apart silently.
    """

    def setUp(self):
        self.workflow = (REPO_ROOT / ".github/workflows/publish-registry.yml").read_text()
        self.pushed = set(re.findall(r"(ghcr\.io/[\w./-]+):", self.workflow))
        self.assertEqual(len(self.pushed), 1, f"expected one image path, got {self.pushed}")
        self.image = self.pushed.pop()

    def test_every_image_the_readme_names_is_the_published_one(self):
        # Every reference, not merely one: a partial rename leaves the README
        # telling a reader to pull one path and run another.
        referenced = set(re.findall(r"ghcr\.io/[\w./-]+", README.read_text()))
        self.assertTrue(referenced)
        for path in referenced:
            self.assertEqual(path.split(":")[0].rstrip("`"), self.image)

    def test_the_registry_identifier_names_the_published_image(self):
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        self.assertTrue(package["identifier"].startswith(self.image + ":"))
        self.assertEqual(package["transport"]["type"], "stdio")
        self.assertEqual(package["registryType"], "oci")

    def test_tags_carry_the_v_prefix_and_latest_moves(self):
        # Both are stated in the README, and both are the workflow's choice.
        self.assertIn(f"{self.image}:${{{{ github.ref_name }}}}", self.workflow)
        self.assertIn(f"{self.image}:latest", self.workflow)


class TestTheRegistryDeclaresTheCredentials(unittest.TestCase):
    def test_the_credential_variables_are_declared(self):
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        declared = {v["name"] for v in package.get("environmentVariables", [])}
        self.assertEqual(
            declared,
            {
                "TICKTICK_CLIENT_ID",
                "TICKTICK_CLIENT_SECRET",
                "TICKTICK_USERNAME",
                "TICKTICK_PASSWORD",
            },
        )

    def test_the_secret_ones_are_marked_secret(self):
        # A client that does not know these are secrets may echo them into a
        # config file or a log.
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        secret = {v["name"] for v in package["environmentVariables"] if v.get("isSecret")}
        self.assertEqual(secret, {"TICKTICK_CLIENT_SECRET", "TICKTICK_PASSWORD"})

    def test_no_credential_carries_a_value(self):
        # A `default` or `value` on a credential entry ships that string to the
        # registry, where it is public. Keyed on the JSON structure rather than
        # on `password=`-shaped text, which cannot occur in JSON and so would
        # pass with a real secret sitting in a `default`.
        package = json.loads(SERVER_JSON.read_text())["packages"][0]
        for entry in package["environmentVariables"]:
            self.assertNotIn("default", entry, f"{entry['name']} carries a value")
            self.assertNotIn("value", entry, f"{entry['name']} carries a value")


if __name__ == "__main__":
    unittest.main()
