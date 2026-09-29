"""Exercise the link boundary with synthetic strings and pinned-object responses."""

import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "preview_collector", ROOT / "ops/showcase/links/collect.py"
)
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)

SENTINELS = [
    "synthetic-client-zebra",
    "synthetic-person-otter",
    "private.internal",
    "private.flycast",
    "10.20.30.40",
    "fdaa:0:1234:a7b::2",
    "e784e9d4b12345",
    "vol_abc123def456",
    "pgdata",
    "registry.fly.io/synthetic:deployment",
    "sk_" + "live_" + "abcdefghijklmnop",
    "EXAMPLE_SECRET",
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "api_key=" + "x" * 48,
    "postgresql://fixture:fixture@host/db",
    "$123",
    "raw.githubusercontent.com",
    "token=short",
    "postgresql://fixture:fixture@127.0.0.1/db",
    "postgresql://user:password@host/db",
]

HARD_CONTENT = {
    "email": ["synthetic-reader@example.invalid"],
    "credential": [
        "token=synthetic-credential",
        "postgresql://fixture:synthetic-password@database.invalid/fixture",
        "mysql://fixture:synthetic-password@database.invalid/fixture",
        "api_key=" + "x" * 48,
        "api_key=" + "x" * 44 + "data",
        "api_key=" + "Ab12+/" * 8 + "==",
        "api_key=" + "".join(chr(code) for code in [81, 55, 109, 90, 50, 97, 76, 57, 118, 66, 52, 99, 78, 56, 120, 82, 54, 116, 89, 51, 107, 80, 53, 119, 72, 49, 106, 68, 48, 115, 70, 113]),
        "api_key=AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEf",
        "gho_" + "x" * 24,
        "sk-" + "a" * 40,
        "pk_live_" + "a" * 40,
        "xoxb-" + "x" * 24,
        "AKIA" + "A1" * 8,
        "FlyV1 " + "x" * 24,
        "sk_" + "live_" + "abcdefghijklmnop",
        "ghp_" + "x" * 36,
        "github_pat_" + "x" * 40,
        "Bearer " + "x" * 32,
        "eyJ" + "x" * 12 + "." + "y" * 16 + "." + "z" * 16,
        "-----BEGIN " + "OPENSSH PRIVATE " + "KEY-----",
    ],
    "price": ["$123", "€12.34", "19 USD", "GBP 20", "12 dollars", "₹123", "123 INR"],
    "home_path": ["/home/synthetic-reader/project", "/" + "Users" + "/synthetic-reader/project"],
}
SENTINELS.extend(value for values in HARD_CONTENT.values() for value in values)

RECORDED_CONTENT = {
    "internal_host": ["synthetic.internal", "synthetic.flycast"],
    "ip_address": ["127.0.0.1", "10.20.30.40", "fdaa:0:1234:a7b::2", "::1"],
    "env_name": ["EXAMPLE_SECRET", "EXAMPLE_TOKEN"],
    "machine_id": ["e784e9d4b12345"],
    "volume_id": ["vol_abc123def456", "pgdata"],
    "image_id": ["registry.fly.io/synthetic:deployment", "sha256:" + "a" * 64],
}


class Links(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in [links.LIB, links.LINKS]:
            (self.root / directory).mkdir(parents=True)
        for name in [
            "public-hosts.json",
            "card-words.json",
            "sensitive-patterns.json",
            "linked-content-policy.json",
            "lineage.generated.json",
            "source-registry.generated.json",
        ]:
            shutil.copyfile(ROOT / links.LIB / name, self.root / links.LIB / name)
        (self.root / links.LINKS / "name-exemptions.json").write_text("[]\n")
        original = links.read(ROOT / links.LINKS / "links.json")
        original["links"].append({
            "id": "test-service", "label": "Open service", "category": "provider",
            "destination": {"host": "https://service.example.invalid"}, "access": "public",
            "surfaces": ["stack"], "thumbnail": "door", "nouns": ["Service", "Health"],
            "highlights": [], "headings": [], "line_marker": None,
        })
        (self.root / links.LIB / "public-hosts.json").write_text(json.dumps([{
            "name": "Test service", "host": "https://service.example.invalid",
            "health": "https://service.example.invalid/health",
        }]))
        selected = [
            "stack-code-warehouse",
            "team-1-contributing",
            "stack-open-console",
            "test-service",
        ]
        self.manifest = {
            **original,
            "links": [
                next(row for row in original["links"] if row["id"] == key)
                for key in selected
            ],
        }
        self.manifest["links"][1]["headings"] = ["guide"]
        self.manifest["links"][1]["destination"]["anchor"] = "guide"
        self.write_manifest()
        self.content = "# Guide\n\nOpen the guide.\n"
        self.names = ["Dockerfile", "boot", "README.md"]
        self.subject = "Document the guide"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.git_patch = patch.object(links, "git", self.git)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def write_manifest(self):
        (self.root / links.LINKS / "links.json").write_text(json.dumps(self.manifest))

    def git(self, repo, *args):
        if args[0] == "rev-parse":
            return "false" if "--is-shallow-repository" in args else "a" * 40
        if args[0] == "cat-file":
            return "tree" if args[-1].endswith("ops/fly/postgres") else "blob"
        if args[0] == "ls-tree":
            return "\0".join(self.names) + "\0"
        if args[0] == "log":
            return self.subject
        if args[0] == "show":
            return self.content
        raise AssertionError(args[0])

    def collect(self):
        with redirect_stderr(io.StringIO()):
            return links.collect(self.root, self.root)

    def test_ci_has_no_network_or_credentials(self):
        with patch.object(
            links.urllib.request, "build_opener", side_effect=AssertionError("network")
        ):
            value = self.collect()
        self.assertEqual(value["names"], "names_not_checked")
        self.assertEqual(value["tenants"], "not_checked")
        self.assertTrue(
            all(e["probe"]["note"] == "Not checked at deploy" for e in value["entries"])
        )
        self.assertNotIn("unreviewed", json.dumps(value))

    def test_hashes_and_slugs_are_not_opaque_tokens(self):
        for value in [
            "0123456789abcdef" * 2 + "01234567",
            "0123456789abcdef" * 4,
            "open-the-source-and-check-the-longer-heading",
            "source-12345678-preview",
            "00000000-0000-0000-0000-000000000000",
        ]:
            self.content = "# Guide\napi_key=" + value
            self.collect()
            links.scan({"secret": value}, self.root, [], "links")
        for value in [
            "".join(chr(code) for code in [81, 55, 109, 90, 50, 97, 76, 57, 118, 66, 52, 99, 78, 56, 120, 82, 54, 116, 89, 51, 107, 80, 53, 119, 72, 49, 106, 68, 48, 115, 70, 113]),
            "AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEf",
            "ghp_" + "a" * 40,
            "eyJ" + "x" * 12 + "." + "y" * 16 + "." + "z" * 16,
            "abcdefghijklmno-123456789-pqrstuvwxyz",
            "0123456789ABCDEF" * 2 + "01234567",
            "a" * 40 + "==",
        ]:
            self.content = "# Guide\napi_key=" + value
            with self.assertRaisesRegex(links.Refused, "links_content_credential"):
                self.collect()
            with self.assertRaisesRegex(links.Refused, "links_sensitive"):
                links.scan({"secret": value}, self.root, [], "links")

    def test_opaque_tokens_need_secret_context_in_linked_blobs(self):
        token = "".join(chr(code) for code in [81, 55, 109, 90, 50, 97, 76, 57, 118, 66, 52, 99, 78, 56, 120, 82, 54, 116, 89, 51, 107, 80, 53, 119, 72, 49, 106, 68, 48, 115, 70, 113])
        link = self.manifest["links"][1]
        link.update(thumbnail="file", headings=[], line_marker=None)
        link["destination"]["anchor"] = None
        self.manifest["links"] = [link]
        cases = [
            ("csv", f"playlist_id\npl.{'ab12' * 8}\n", False),
            ("csv", f"playlist_id,secret\n{token},reviewed\n", False),
            ("csv", f"playlist_id,secret\nreviewed,{token}\n", True),
            ("csv", f'playlist_id,"API Key"\nreviewed,"{token}"\n', True),
            ("json", json.dumps({"playlist_id": token}, indent=2), False),
            ("json", '{"apiKey":\n"' + token + '"}', True),
            ("json", json.dumps({"credentials": {"value": [token]}}), True),
            ("yaml", f"playlist_id: {token}\n", False),
            ("yaml", f"password: |\n  {token}\n", True),
            ("yaml", f"auth:\n  values:\n    - {token}\n", True),
            ("yaml", f"auth: &cycle\n  nested: *cycle\n  value: {token}\n", True),
            ("txt", token, False),
            ("txt", f"api_key={token}", True),
            ("txt", f"X-Api-Key: {token}", True),
            ("txt", f"A private value is {token}.", True),
            ("txt", f"secret\n{token}", False),
            ("txt", "ghp_" + "x" * 36, True),
            ("txt", "eyJ" + "x" * 12 + "." + "y" * 16 + "." + "z" * 16, True),
            ("txt", "-----BEGIN " + "OPENSSH PRIVATE " + "KEY-----", True),
        ]
        for index, (suffix, text, refused) in enumerate(cases):
            with self.subTest(index=index):
                link["destination"]["path"] = f"docs/synthetic.{suffix}"
                self.write_manifest()
                self.content = text
                if refused:
                    with self.assertRaisesRegex(
                        links.Refused, "links_content_credential"
                    ):
                        self.collect()
                else:
                    self.collect()
        for name in links.read(self.root / links.LIB / "linked-content-policy.json")[
            "opaque_token"
        ]["secret_words"]:
            self.assertIn(
                "credential",
                links.content_classes({name.upper(): token}, self.root, []),
            )
        for key in ["monkey", "author", "playlist_id"]:
            links.scan({key: token}, self.root, [], "links")
        with self.assertRaisesRegex(links.Refused, "links_sensitive"):
            links.scan({"credentials": {"value": [token]}}, self.root, [], "links")

    def test_sentinels_in_every_text_boundary(self):
        os.environ["MDP_SHOWCASE_DENY_NAMES"] = "\n".join(SENTINELS[:2])
        baseline = copy.deepcopy(self.manifest)
        for sentinel in SENTINELS:
            for field in ["nouns", "highlights", "headings"]:
                with self.subTest(field=field, sentinel=SENTINELS.index(sentinel)):
                    self.manifest = copy.deepcopy(baseline)
                    self.manifest["links"][0][field] = (
                        [sentinel, "guide"] if field == "nouns" else [sentinel]
                    )
                    self.write_manifest()
                    with self.assertRaises(links.Refused):
                        self.collect()
            self.manifest = copy.deepcopy(baseline)
            self.write_manifest()
            value = self.collect()
            for field in ["body", "proper_names", "headings"]:
                changed = copy.deepcopy(value)
                changed["entries"][0][field].append(sentinel)
                with self.assertRaises(links.Refused):
                    links.validate(changed, self.root)
            value["entries"][-1]["probe"]["note"] = sentinel
            with self.assertRaises(links.Refused):
                links.validate(value, self.root)

    def test_subject_scan_and_full_history(self):
        os.environ["MDP_SHOWCASE_DENY_NAMES"] = SENTINELS[0]
        self.subject = "Update " + SENTINELS[0]
        with self.assertRaisesRegex(links.Refused, "stack-code-warehouse links_name"):
            self.collect()
        self.subject = "Document the guide"
        original = self.git

        def shallow(repo, *args):
            return (
                "true" if "--is-shallow-repository" in args else original(repo, *args)
            )

        with patch.object(links, "git", side_effect=shallow):
            self.assertEqual(self.collect()["names"], "names_not_checked")

    def test_landing_content_records_infrastructure_without_values(self):
        for category, values in RECORDED_CONTENT.items():
            for sentinel in values:
                with self.subTest(category=category):
                    self.content = "# Guide\n" + sentinel
                    captured = io.StringIO()
                    with redirect_stderr(captured):
                        value = links.collect(self.root, self.root)
                    self.assertIn(
                        f"stack-code-warehouse content_{category}", captured.getvalue()
                    )
                    self.assertNotIn(sentinel, captured.getvalue())
                    self.assertNotIn(sentinel, json.dumps(value))
                    self.names.append(sentinel)
                    self.collect()
                    self.names.pop()
                    self.subject = sentinel
                    os.environ["MDP_SHOWCASE_DENY_NAMES"] = SENTINELS[0]
                    self.collect()
                    self.subject = "Document the guide"

    def test_landing_content_refuses_names_and_hard_classes(self):
        os.environ["MDP_SHOWCASE_DENY_NAMES"] = "\n".join(SENTINELS[:2])
        for category, values in {**HARD_CONTENT, "name": SENTINELS[:2]}.items():
            for sentinel in values:
                with self.subTest(category=category):
                    expected = (
                        "links_name"
                        if category == "name"
                        else f"links_content_{category}"
                    )
                    self.content = "# Guide\n" + sentinel
                    with self.assertRaisesRegex(links.Refused, expected):
                        self.collect()
                    self.content = "# Guide\nOpen the guide."
                    self.names.append(sentinel)
                    with self.assertRaisesRegex(links.Refused, expected):
                        self.collect()
                    self.names.pop()
                    self.subject = sentinel
                    with self.assertRaisesRegex(links.Refused, expected):
                        self.collect()
                    self.subject = "Document the guide"

    def test_credential_next_to_an_identifier_still_fails(self):
        for identifier in [
            "sha256:" + "a" * 64,
            "registry.fly.io/synthetic:" + "b" * 48,
            "c" * 48 + ".internal",
        ]:
            self.content = "# Guide\n" + identifier
            self.collect()
            for credential in HARD_CONTENT["credential"]:
                self.content = f"# Guide\n{identifier}\n{credential}"
                with self.assertRaisesRegex(links.Refused, "links_content_credential"):
                    self.collect()

    def test_database_credentials_always_refuse_in_linked_blobs(self):
        key = "ghp_" + "x" * 36
        values = [
            "postgresql://fixture:fixture@127.0.0.1:5432/db",
            "postgresql://fixture:fixture@[::1]:5432/db",
            "postgresql://fixture:fixture@localhost/db?host=db.example.net",
            "mysql://fixture:fixture@localhost/db",
            "postgresql://user:password@host/db",
            "postgresql://username:password@hostname/db",
            "postgresql://USER:PASS@HOST/db",
            "postgresql://user:...@host/db",
            "postgresql://fixture:different@localhost/db",
            "postgresql://fixture:fixture@database.example.net/db",
            f"postgresql://{key}:{key}@localhost/db",
        ]
        for index, value in enumerate(values):
            with self.subTest(index=index):
                self.content = '# Guide\n"' + value + '"\n'
                with self.assertRaisesRegex(links.Refused, "links_content_credential"):
                    self.collect()

    def test_synthetic_markers_never_exempt_credentials(self):
        link = self.manifest["links"][1]
        link["destination"].update(path="test/fixtures/auth.py", anchor=None)
        link.update(thumbnail="file", headings=[])
        self.manifest["links"] = [link]
        self.write_manifest()
        for index, value in enumerate(HARD_CONTENT["credential"]):
            with self.subTest(index=index):
                digest = hashlib.sha256(value.encode()).hexdigest()
                self.content = (
                    f"# mdp-showcase-synthetic-credential sha256:{digest}\n"
                    f"value = {json.dumps(value)}\n"
                )
                with self.assertRaisesRegex(links.Refused, "links_content_credential"):
                    self.collect()

    def test_csv_headers_preserve_each_original_field(self):
        token = "".join(chr(code) for code in [81, 55, 109, 90, 50, 97, 76, 57, 118, 66, 52, 99, 78, 56, 120, 82, 54, 116, 89, 51, 107, 80, 53, 119, 72, 49, 106, 68, 48, 115, 70, 113])
        link = self.manifest["links"][1]
        link["destination"].update(path="docs/synthetic.csv", anchor=None)
        link.update(thumbnail="file", headings=[])
        self.manifest["links"] = [link]
        self.write_manifest()
        for index, value in enumerate(
            [
                f"secret.internal\n{token}\n",
                f"secret,secret\n{token},reviewed\n",
                f"secret, SECRET \n{token},reviewed\n",
                f"api_key,API-KEY\n{token},reviewed\n",
                f"???,playlist_id\n{token},reviewed\n",
                f",playlist_id\n{token},reviewed\n",
                f'"",playlist_id\n{token},reviewed\n',
                f"playlist_id\nreviewed,{token}\n",
                "playlist_id,secret\nreviewed\n",
                'playlist_id,secret\nreviewed,"unterminated\n',
                "",
            ]
        ):
            with self.subTest(index=index):
                self.content = value
                with self.assertRaisesRegex(links.Refused, "links_content_credential"):
                    self.collect()
        self.content = f'playlist_id,secret\n"{token}",reviewed\n'
        self.collect()
        self.content = f'playlist_id,secret\nreviewed,"line one\n{token}"\n'
        with self.assertRaisesRegex(links.Refused, "links_content_credential"):
            self.collect()

    def test_credential_audit_reports_paths_without_values(self):
        sentinel = "api_key=" + "x" * 48
        self.content = "# Guide\n" + sentinel
        captured = io.StringIO()
        with redirect_stderr(captured):
            result = links.audit(self.root, self.root)
        self.assertEqual(
            result["credential_review"]["stack-code-warehouse"],
            {
                "status": "refused",
                "refused_files": ["ops/fly/postgres/README.md"],
            },
        )
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertNotIn(sentinel, captured.getvalue())
        self.content = "# Guide\nOpen the guide.\n"
        with redirect_stderr(io.StringIO()):
            result = links.audit(self.root, self.root)
        self.assertEqual(result["credential_review"], {})

    def test_rejected_identifiers_never_reach_diagnostics(self):
        os.environ["MDP_SHOWCASE_DENY_NAMES"] = SENTINELS[0]
        original = links.collect
        for identifier in [
            SENTINELS[0],
            "SYNTHETIC INVALID ID",
            "valid-id\n",
            "X" * 48,
        ]:
            self.manifest["links"][0]["id"] = identifier
            self.write_manifest()
            captured = io.StringIO()
            with (
                patch.object(
                    links,
                    "collect",
                    side_effect=lambda **kwargs: original(self.root, **kwargs),
                ),
                patch.object(
                    sys,
                    "argv",
                    ["collect.py", "--output", str(self.root / "output.json")],
                ),
                redirect_stderr(captured),
                self.assertRaises(SystemExit) as caught,
            ):
                links.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertRegex(captured.getvalue(), r"^links links_[a-z_]+\n$")
            self.assertNotIn(identifier, captured.getvalue())
        self.assertEqual(str(links.Refused(SENTINELS[0])), "links links_invalid")
        self.assertEqual(str(links.Refused("BAD ID")), "links links_invalid")

    def test_noreply_bots_are_allowed_but_personal_noreply_handles_are_not(self):
        for value in [
            "noreply@example.invalid",
            "no-reply@example.invalid",
            "42+synthetic-check[bot]@users.noreply.github.com",
        ]:
            self.assertNotIn("email", links.content_classes(value, self.root, []))
        self.assertIn(
            "email",
            links.content_classes(
                "synthetic-person@users.noreply.github.com", self.root, []
            ),
        )

    def test_money_is_distinct_from_shell_and_sql_parameters(self):
        examples = [
            ("code.sh", 'input=$1; printf "%s" "$2" # $123\n'),
            ("code.sql", "SELECT $1, '$123' -- $2\n"),
            ("code.sql", "SELECT $1, $$price $123$$, $tag$price $123$tag$"),
            ("code.py", '# A comment says "SELECT $123".\nquery = "SELECT $1"'),
            ("code.yml", "steps:\n  - run: \"echo '$123'; echo $1\""),
            ("code.ts", "const query = \"SELECT $1, '$123' WHERE value=$2\";"),
            (
                "code.ts",
                '// "quoted comment"\nconst query = `AND item.day = $1::date`; const price = "$123";',
            ),
            (
                "code.yml",
                'steps:\n  - run: |\n      printf "%s" "$1"\n      echo \'$123\'\n',
            ),
            ("code.md", '```sh\necho "$1"\n```\nThe charge is $123.\n'),
        ]
        for path, text in examples:
            with self.subTest(path=path):
                self.assertIn("price", links.content_classes(text, self.root, [], path))
                without_money = text.replace("$123", "a reviewed amount").replace(
                    "-- $2", "-- note"
                )
                self.assertNotIn(
                    "price", links.content_classes(without_money, self.root, [], path)
                )
        self.assertIn(
            "price",
            links.content_classes('echo "SELECT $123"', self.root, [], "guide.md"),
        )
        self.assertIn(
            "price",
            links.content_classes('cost: "$123"', self.root, [], "settings.yml"),
        )
        self.assertIn(
            "price", links.content_classes("echo \\$123", self.root, [], "code.sh")
        )
        with self.assertRaises(links.Refused):
            links.scan("$1", self.root, [], "synthetic-link")
        self.assertNotIn(
            "price", links.content_classes('input="$1.txt"', self.root, [], "code.sh")
        )

    def test_audit_counts_each_link_once_per_class(self):
        self.content = "# Guide\n127.0.0.1 127.0.0.1 EXAMPLE_SECRET"
        captured = io.StringIO()
        with redirect_stderr(captured):
            value = links.audit(self.root, self.root)
        self.assertEqual(value["classes"]["ip_address"], 2)
        self.assertEqual(value["classes"]["env_name"], 2)
        self.assertNotIn("127.0.0.1", captured.getvalue())
        self.assertNotIn("EXAMPLE_SECRET", json.dumps(value))

    def exempt_guide(self):
        self.manifest["links"] = [self.manifest["links"][1]]
        self.write_manifest()
        path = self.manifest["links"][0]["destination"]["path"]
        review = {
            "path": path,
            "reason": "public registry of rights-holding organizations",
            "reviewed_on": "2026-09-27",
        }
        (self.root / links.LINKS / "name-exemptions.json").write_text(
            json.dumps([review])
        )
        os.environ["MDP_SHOWCASE_DENY_NAMES"] = SENTINELS[0]
        return review

    def test_reviewed_file_exempts_names_and_records_only_counts(self):
        self.exempt_guide()
        self.content = f"# Guide\n{SENTINELS[0]}\n{SENTINELS[0].upper()}\n"
        captured = io.StringIO()
        with redirect_stderr(captured):
            value = links.collect(self.root, self.root)
            audit = links.audit(self.root, self.root)
        self.assertEqual(value["entries"][0]["exempted_name_matches"], 2)
        self.assertEqual(audit["exempted_name_matches"], 2)
        self.assertEqual(audit["classes"]["name"], 0)
        for output in [captured.getvalue(), json.dumps(value), json.dumps(audit)]:
            self.assertNotIn(SENTINELS[0], output.casefold())
        self.manifest["links"][0]["destination"]["path"] += ".copy"
        self.write_manifest()
        with self.assertRaisesRegex(links.Refused, "links_name"):
            self.collect()

    def test_review_never_exempts_other_hard_classes_or_preview_words(self):
        self.exempt_guide()
        for category, sentinels in HARD_CONTENT.items():
            for sentinel in sentinels:
                with self.subTest(category=category):
                    self.content = f"# Guide\n{SENTINELS[0]}\n{sentinel}"
                    with self.assertRaisesRegex(
                        links.Refused, f"links_content_{category}"
                    ):
                        self.collect()
        self.content = f"# Guide\n{SENTINELS[0]}"
        value = self.collect()
        value["entries"][0]["body"].append(SENTINELS[0])
        with self.assertRaisesRegex(links.Refused, "links_name"):
            links.validate(value, self.root)
        self.subject = SENTINELS[0]
        with self.assertRaisesRegex(links.Refused, "links_name"):
            self.collect()

    def test_review_is_exact_validated_and_bound_to_the_artifact(self):
        review = self.exempt_guide()
        value = self.collect()
        path = self.root / links.LINKS / "name-exemptions.json"
        path.write_text(json.dumps([{**review, "reviewed_on": "2026-09-28"}]))
        with self.assertRaisesRegex(links.Refused, "links_inputs"):
            links.validate(value, self.root)
        for changes in [
            {"path": "../guide.md"},
            {"path": "docs/*"},
            {"reason": ""},
            {"reviewed_on": "2026-02-30"},
            {"credential": True},
        ]:
            path.write_text(json.dumps([{**review, **changes}]))
            with self.assertRaises(links.Refused):
                self.collect()
        for invalid in [review, [review, review]]:
            path.write_text(json.dumps(invalid))
            with self.assertRaises(links.Refused):
                self.collect()

    def test_missing_path_heading_marker_highlight_pin_and_host(self):
        baseline = copy.deepcopy(self.manifest)
        for change, result in [
            (
                lambda: self.manifest["links"][1]["headings"].append("absent"),
                "links_anchor",
            ),
            (
                lambda: self.manifest["links"][1].update(line_marker="missing marker"),
                "links_line",
            ),
            (
                lambda: self.manifest["links"][0]["highlights"].append("absent"),
                "links_invalid",
            ),
            (
                lambda: self.manifest["links"][-1]["destination"].update(
                    host="https://example.invalid"
                ),
                "links_host",
            ),
        ]:
            self.manifest = copy.deepcopy(baseline)
            change()
            self.write_manifest()
            with self.assertRaises((links.Refused, ValueError)):
                self.collect()
        self.manifest = baseline
        self.write_manifest()
        with (
            patch.object(
                links,
                "git",
                side_effect=links.Refused("links", "links_git_unavailable"),
            ),
            self.assertRaises(links.Refused),
        ):
            self.collect()
    def test_tenants_need_review(self):
        os.environ["MDP_SHOWCASE_TENANT_COUNT"] = "1"
        with self.assertRaisesRegex(
            links.Refused, "stack-open-console tenants_present"
        ):
            self.collect()
        self.manifest["console_tenant_review"] = "Reviewed console access"
        self.write_manifest()
        self.assertEqual(self.collect()["tenants"], "checked")

    def test_freshness_uses_collection_date(self):
        value = self.collect()
        value["collected_at"] = "2000-01-01T00:00:00Z"
        with self.assertRaisesRegex(links.Refused, "links_stale"):
            links.validate(value, self.root)
        links.validate(value, self.root, image=True)

    def test_word_gate_counts_proper_names(self):
        for body, proper in [
            (["rows"], []),
            (["word"] * 13, []),
            (["guide"], ["a_b_c_d_e_f_g_h_i_j_k_l"]),
            (["guide"], ["1", "2", "3"]),
        ]:
            with self.assertRaisesRegex(links.Refused, "links_words"):
                links.word_gate(body, proper, self.root, "synthetic-link")
        links.word_gate(["guide"], ["mart_example.sql"], self.root, "synthetic-link")

    def test_host_failure_is_soft_and_uses_health_allowlist(self):
        os.environ["MDP_SHOWCASE_PROBE_HOSTS"] = "1"
        with patch.object(links.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError()
            value = self.collect()
            self.assertEqual(value["entries"][-1]["probe"]["state"], "no_answer")
            self.assertEqual(
                opener.return_value.open.call_args.args[0],
                "https://service.example.invalid/health",
            )

    def test_malformed_health_response_stays_soft(self):
        os.environ["MDP_SHOWCASE_PROBE_HOSTS"] = "1"
        with patch.object(links.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = links.HTTPException(
                "synthetic response"
            )
            value = self.collect()
            self.assertEqual(value["entries"][-1]["probe"]["state"], "no_answer")

    def test_no_payload_or_exception_text_in_logs(self):
        os.environ["MDP_SHOWCASE_DENY_NAMES"] = SENTINELS[0]
        self.content = SENTINELS[0]
        with self.assertRaises(links.Refused) as caught:
            self.collect()
        self.assertNotIn(SENTINELS[0], str(caught.exception))


if __name__ == "__main__":
    unittest.main()
