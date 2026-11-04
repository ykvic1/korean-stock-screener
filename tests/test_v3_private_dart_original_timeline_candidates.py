"""Synthetic-only tests: document dates remain private, unverified candidates."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import unittest

import v3_private_dart_original_timeline_candidates as timeline


def source(body, name="document.xml", encoding="utf-8"):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(name, body.encode(encoding))
    return stream.getvalue()


class OriginalTimelineCandidateTests(unittest.TestCase):
    def test_date_cues_are_candidates_not_authenticated_effective_dates(self):
        raw = source(
            "<DOCUMENT><TITLE>정정신고서</TITLE>"
            "<TABLE><TR><TD>합병기일</TD><TD>2026년 11월 04일</TD></TR>"
            "<TR><TD>변경상장예정일</TD><TD>2026.11.09</TD></TR>"
            "</TABLE></DOCUMENT>"
        )
        result = timeline.inspect_original_zip(raw)
        self.assertEqual(result["inspection_status"], "candidate_dates_proposed")
        self.assertEqual(
            {(r["label"], r["candidate_date"]) for r in result["candidate_dates"]},
            {("합병기일", "2026-11-04"), ("변경상장예정일", "2026-11-09")}
        )
        self.assertTrue(result["correction_keyword_mentioned_not_revision_proof"])
        self.assertFalse(result["filing_revision_link_authenticated"])
        self.assertFalse(result["effective_date_authenticated"])
        self.assertFalse(result["corporate_action_authenticated"])
        self.assertFalse(result["scores_authorized"])
        self.assertNotIn("정정신고서", str(result))
        self.assertNotIn("document.xml", str(result))
        self.assertEqual(timeline.validate_candidate_summary(result), result)

    def test_composite_cue_is_not_double_counted_as_plain_merger(self):
        result = timeline.inspect_original_zip(
            source("<DOCUMENT>분할합병기일 2026.11.04</DOCUMENT>")
        )
        self.assertEqual(
            [(r["label"], r["candidate_date"]) for r in result["candidate_dates"]],
            [("분할합병기일", "2026-11-04")]
        )
        self.assertFalse(result["effective_date_authenticated"])
        timeline.validate_candidate_summary(result)

    def test_missing_or_invalid_date_never_borrows_neighbor_field_date(self):
        for body in (
            "합병기일 분할기일 2026-11-04",
            "합병기일 2026-02-30 분할기일 2026-11-04",
        ):
            with self.subTest(body=body):
                result = timeline.inspect_original_zip(
                    source("<DOCUMENT>" + body + "</DOCUMENT>")
                )
                self.assertEqual(
                    [(r["label"], r["candidate_date"])
                     for r in result["candidate_dates"]],
                    [("분할기일", "2026-11-04")]
                )
                timeline.validate_candidate_summary(result)

        invalid_first = timeline.inspect_original_zip(
            source("<DOCUMENT>합병기일 2026-02-30 이후 2026-11-04</DOCUMENT>")
        )
        self.assertEqual(invalid_first["candidate_dates"], [])
        self.assertFalse(invalid_first["effective_date_authenticated"])

    def test_cp949_document_and_compact_date_are_supported(self):
        raw = source(
            "<DOCUMENT>주식분할기준일 20261104</DOCUMENT>",
            encoding="cp949"
        )
        result = timeline.inspect_original_zip(raw)
        self.assertEqual(result["candidate_dates"][0]["candidate_date"], "2026-11-04")
        self.assertEqual(result["candidate_dates"][0]["label"], "주식분할기준일")

    def test_missing_or_unreadable_is_not_negative_corporate_action_evidence(self):
        for body in (
            "<DOCUMENT>공시 전문과 일정은 별도 확인 요망</DOCUMENT>",
            "<!DOCTYPE DOCUMENT [<!ENTITY x 'bad'>]><DOCUMENT>&x;</DOCUMENT>",
        ):
            result = timeline.inspect_original_zip(source(body))
            self.assertEqual(result["candidate_dates"], [])
            self.assertFalse(result["corporate_action_authenticated"])
            self.assertFalse(result["effective_date_authenticated"])
            self.assertFalse(result["scores_authorized"])
            timeline.validate_candidate_summary(result)
        self.assertEqual(
            timeline.inspect_original_zip(
                source("<DOCUMENT>공시 전문과 일정은 별도 확인 요망</DOCUMENT>")
            )["inspection_status"], "no_supported_date_cue"
        )

    def test_invalid_dates_cannot_be_misreported_as_candidates(self):
        result = timeline.inspect_original_zip(
            source("<DOCUMENT>분할기일 2026.02.30</DOCUMENT>")
        )
        self.assertEqual(result["candidate_dates"], [])
        self.assertEqual(result["inspection_status"], "no_supported_date_cue")

    def test_corrupted_or_oversharing_summary_is_rejected(self):
        good = timeline.inspect_original_zip(
            source("<DOCUMENT>효력발생일 2026-11-04</DOCUMENT>")
        )
        variants = [
            {**good, "scores_authorized": True},
            {**good, "source_text": "secret"},
            {**good, "candidate_dates": [{
                **good["candidate_dates"][0], "candidate_date": "2026-02-30"
            }]},
            {**good, "source_members_decoded": -1},
        ]
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    timeline.TimelineCandidateError,
                    "invalid_private_timeline_candidate_summary"
                ):
                    timeline.validate_candidate_summary(value)

    def test_non_zip_cannot_be_interpreted(self):
        with self.assertRaisesRegex(
            timeline.TimelineCandidateError, "invalid_original_timeline_source"
        ):
            timeline.inspect_original_zip(b"private-not-zip")


if __name__ == "__main__":
    unittest.main()
