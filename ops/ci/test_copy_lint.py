"""A returning performative headline, mannered phrase or long-form Ops must fail the lint."""

import tempfile
import unittest
from pathlib import Path

import lint_copy as lint


class CopyLintTest(unittest.TestCase):
    def write(self, root: Path, name: str, content: str):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/app/today/page.tsx",
                "<h1>{songs ? 'new movement.' : 'no movement yet.'}</h1>",
            )
            self.write(
                root,
                "control/apps/showcase/app/songs/page.tsx",
                '<h1>where it\'s spreading.</h1>',
            )
            self.write(
                root, "README.md", "Use [Ops](docs/operating.md) for routine tasks."
            )
            self.assertEqual(lint.find_violations(root), [])

    def test_brand_perception_verb_headline_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/app/today/page.tsx",
                "<h1>mdp records the next arrival.</h1>",
            )
            rules = {rule for _, _, rule, _ in lint.find_violations(root)}
            self.assertIn("brand-verb", rules)

    def test_brand_as_sentence_subject_fails_anywhere(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/packages/contracts/src/source-wording.ts",
                'plain: "Music Data Platform sorts records by their source."',
            )
            self.write(
                root,
                "control/apps/showcase/components/behind-card.tsx",
                '<p className="eyebrow">how mdp found it</p>',
            )
            found = [(path, rule) for path, _, rule, _ in lint.find_violations(root)]
            self.assertIn(
                ("control/packages/contracts/src/source-wording.ts", "brand-subject"),
                found,
            )
            self.assertIn(
                ("control/apps/showcase/components/behind-card.tsx", "brand-subject"),
                found,
            )

    def test_leading_the_headline_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/components/holdings.tsx",
                "<h1>the numbers become insight.</h1>",
            )
            rules = {rule for _, _, rule, _ in lint.find_violations(root)}
            self.assertIn("leading-the", rules)

    def test_mannered_phrase_in_heading_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/app/holdings/page.tsx",
                '<h1>the music leaves an asset.</h1>',
            )
            rules = {rule for _, _, rule, _ in lint.find_violations(root)}
            self.assertIn("mannered-phrase", rules)

    def test_long_form_operations_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/control-api/src/pages.tsx",
                '<a href="/ops">Operations</a>',
            )
            rules = {rule for _, _, rule, _ in lint.find_violations(root)}
            self.assertIn("long-form-ops", rules)

    def test_console_operations_identifier_is_not_a_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/server/console-operations.ts",
                'export class ConsoleOperations {}\nthrow new Error("Operation list is full.");',
            )
            self.assertEqual(lint.find_violations(root), [])

    def test_body_text_can_use_an_article_and_belong_to_a_team(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/app/page.tsx",
                "<p>This playlist belongs to the team.</p>",
            )
            self.assertEqual(lint.find_violations(root), [])

    def test_regression_quotes_and_comments_are_not_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/test/copy.test.tsx",
                "<h1>mdp records new music</h1>",
            )
            self.write(
                root,
                "control/apps/showcase/app/page.tsx",
                "// <h1>the music leaves an asset.</h1>\n<p>Open music.</p>",
            )
            self.assertEqual(lint.find_violations(root), [])

    def test_documented_outage_headline_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/app/page.tsx",
                "<h1>the music is out of reach.</h1>",
            )
            self.assertEqual(lint.find_violations(root), [])

    def test_secondary_heading_leading_article_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "control/apps/showcase/app/page.tsx",
                "<h2>the oldest fact on this screen.</h2>",
            )
            self.assertIn(
                "leading-the", {rule for _, _, rule, _ in lint.find_violations(root)}
            )

    def test_allowlisted_docs_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "ops/evidence/copy-examples.md",
                "Not `the music leaves an asset`; not `mdp records the next movement`.",
            )
            self.write(root, "ops/CLAUDE.md", "# Operations conventions")
            self.write(
                root,
                "ops/evidence/showcase-app/before-after.md",
                "before: `mdp records the next movement.`",
            )
            self.assertEqual(lint.find_violations(root), [])

    def test_markdown_headlines_fail_at_each_level(self):
        for marker in ("#", "##", "######"):
            with (
                self.subTest(marker=marker),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.write(root, "README.md", f"{marker} The music leaves an asset.\n")
                rules = {rule for _, _, rule, _ in lint.find_violations(root)}
                self.assertEqual(rules, {"leading-the", "mannered-phrase"})

    def test_markdown_underlined_headline_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "docs/guide.md", "The music leaves an asset.\n---\n")
            found = lint.find_violations(root)
            self.assertIn(
                ("docs/guide.md", 1, "mannered-phrase", "The music leaves an asset."),
                found,
            )

    def test_markdown_body_and_fenced_headline_examples_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "README.md",
                "This playlist belongs to the team.\n\n```md\n# The music leaves an asset.\n```\n\n~~~\nThe music leaves an asset.\n===\n~~~\n",
            )
            self.assertEqual(lint.find_violations(root), [])


if __name__ == "__main__":
    unittest.main()


class FunctionCopyTest(unittest.TestCase):
    def check(self, **changes):
        import csv

        row = {
            "source_key": "example",
            "what_it_does": "Reads public chart pages from a fixture source every hour and saves song positions for comparison.",
            "why_it_matters": "Daily comparisons reveal changes in chart positions.",
            "idea_1": "Could compare chart positions across days.",
        } | changes
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / lint.FLOW_COPY
            path.parent.mkdir(parents=True)
            with path.open("w") as file:
                writer = csv.DictWriter(file, fieldnames=row)
                writer.writeheader()
                writer.writerow(row)
            return {rule for _, _, rule, _ in lint.find_violations(Path(directory))}

    def test_plain_function_copy_passes(self):
        self.assertEqual(self.check(), set())

    def test_each_forbidden_word_fails_both_fields(self):
        for word in [
            "bronze",
            "silver",
            "gold",
            "universal",
            "mart",
            "invoke",
            "cycle",
            "tenant",
            "knob",
            "streamline",
            "scope",
            "payload",
            "endpoint",
            "scrape",
            "idle",
        ] + ["switched off", "15 minutes"]:
            for field in ("what_it_does", "why_it_matters"):
                with self.subTest(word=word, field=field):
                    self.assertIn(
                        "function-jargon",
                        self.check(
                            **{
                                field: f"Reads {word} every day to keep a clear record for the team."
                            }
                        ),
                    )

    def test_ideas_stay_possible(self):
        self.assertIn("function-idea", self.check(idea_1="Predicts every hit."))

    def test_sentence_length_and_verb(self):
        self.assertIn("function-length", self.check(what_it_does="Reads profiles."))
        self.assertIn(
            "function-length", self.check(what_it_does="Reads " + "profiles " * 45)
        )
        self.assertIn(
            "function-verb",
            self.check(
                what_it_does="The reader looks at public chart pages every day for the team to compare."
            ),
        )

    def test_product_subject_fails(self):
        self.assertIn(
            "brand-subject",
            self.check(
                what_it_does="Music Data Platform reads public charts from fixture source every hour and saves the counts for comparison."
            ),
        )
