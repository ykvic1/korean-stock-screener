"""Synthetic-only tests: document dates remain private, unverified candidates."""
from io import BytesIO
from datetime import date, timedelta
import copy
from zipfile import ZipFile, ZIP_DEFLATED
import unittest

import v3_private_dart_original_timeline_candidates as timeline


def source(body, name="document.xml", encoding="utf-8"):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(name, body.encode(encoding))
    return stream.getvalue()


class OriginalTimelineCandidateTests(unittest.TestCase):
    def test_correction_original_day_is_separate_from_event_dates(self):
        result = timeline.inspect_original_zip(source(
            "<DOCUMENT><TABLE><TR><TD>정정대상 공시서류의 최초제출일</TD>"
            "<TD>2026년 10월 20일</TD></TR><TR><TD>합 병 기 일</TD>"
            "<TD>2026-11-04</TD></TR></TABLE></DOCUMENT>"))
        self.assertEqual([(r["label"], r["candidate_date"]) for r in result["candidate_dates"]],
                         [("합병기일", "2026-11-04")])
        correction = result["correction_original_filing_date_inspection"]
        self.assertEqual(correction["candidate_dates"][0]["candidate_date"], "2026-10-20")
        self.assertEqual(correction["candidate_dates"][0]["label"], timeline.CORRECTION_LABEL)
        self.assertFalse(correction["revision_link_authenticated"])
        self.assertFalse(result["effective_date_authenticated"])
        timeline.validate_candidate_summary(result)
        legacy = {key: value for key, value in result.items()
                  if key != "correction_original_filing_date_inspection"}
        self.assertEqual(timeline.validate_candidate_summary(legacy), legacy)

    def test_correction_day_cannot_borrow_another_field_or_invalid_later_date(self):
        for body in (
            "<TR><TD>정정대상 공시서류의 최초제출일</TD><TD>미정</TD></TR>"
            "<TR><TD>작성일</TD><TD>2026-11-04</TD></TR>",
            "정정대상 공시서류의 최초제출일 미정 정정사유 2026-11-04 계약 변경",
            "정정대상 공시서류의 최초제출일 2026-02-30 이후 2026-11-04",
        ):
            with self.subTest(body=body):
                result = timeline.inspect_original_zip(source("<DOCUMENT>" + body + "</DOCUMENT>"))
                self.assertEqual(result["correction_original_filing_date_inspection"]["candidate_dates"], [])
                self.assertEqual(result["candidate_dates"], [])
                timeline.validate_candidate_summary(result)

    def test_same_row_unrelated_cell_date_cannot_be_borrowed(self):
        for label, kind in (("합병기일", "candidate_dates"),
                            ("정정대상 공시서류의 최초제출일", "correction")):
            for first_value in ("미정", "-"):
                with self.subTest(label=label, first_value=first_value):
                    raw = source(
                        "<DOCUMENT><TABLE><TR><TH>" + label + "</TH><TD>" + first_value +
                        "</TD><TD>작성일 2026-11-04</TD></TR></TABLE></DOCUMENT>")
                    result = timeline.inspect_original_zip(raw)
                    self.assertEqual(result["candidate_dates"], [])
                    self.assertEqual(result["correction_original_filing_date_inspection"]
                                     ["candidate_dates"], [])
                    timeline.validate_candidate_summary(result)

    def test_same_cell_and_adjacent_value_cell_still_capture_real_dates(self):
        for body in (
            "<TR><TD>합병기일 2026-11-04</TD><TD>작성일 2026-11-09</TD></TR>",
            "<TR><TH>합병기일</TH><TD>2026-11-04</TD><TD>작성일 2026-11-09</TD></TR>",
            "<TR><TH>합병기일 :</TH><TD>2026-11-04</TD><TD>작성일 2026-11-09</TD></TR>",
        ):
            with self.subTest(body=body):
                result = timeline.inspect_original_zip(source("<DOCUMENT>" + body + "</DOCUMENT>"))
                self.assertEqual([(r["label"], r["candidate_date"])
                                  for r in result["candidate_dates"]],
                                 [("합병기일", "2026-11-04")])
                timeline.validate_candidate_summary(result)

    def test_correction_only_document_does_not_invent_an_effective_day(self):
        result = timeline.inspect_original_zip(source(
            "<DOCUMENT>정 정 대 상 공 시 서 류 의 최 초 제 출 일 20261020</DOCUMENT>",
            encoding="cp949"))
        self.assertTrue(result["correction_keyword_mentioned_not_revision_proof"])
        self.assertEqual(result["candidate_dates"], [])
        self.assertEqual(result["inspection_status"], "no_supported_date_cue")
        self.assertEqual(len(result["correction_original_filing_date_inspection"]["candidate_dates"]), 1)
        timeline.validate_candidate_summary(result)

    def test_correction_candidates_are_capped_and_tampered_summary_fails_closed(self):
        body = "".join("<TR><TD>정정대상 공시서류의 최초제출일</TD><TD>" +
                       (date(2026, 1, 1) + timedelta(days=i)).isoformat() + "</TD></TR>"
                       for i in range(40))
        result = timeline.inspect_original_zip(source("<DOCUMENT><TABLE>" + body + "</TABLE></DOCUMENT>"))
        correction = result["correction_original_filing_date_inspection"]
        self.assertEqual(len(correction["candidate_dates"]), timeline.MAX_CANDIDATES)
        self.assertTrue(correction["candidate_list_truncated"])
        timeline.validate_candidate_summary(result)
        for change in ("authenticated", "raw_text", "wrong_label", "invalid_date"):
            bad = copy.deepcopy(result)
            target = bad["correction_original_filing_date_inspection"]
            if change == "authenticated": target["revision_link_authenticated"] = True
            if change == "raw_text": target["source_text"] = "private raw source"
            if change == "wrong_label": target["candidate_dates"][0]["label"] = "효력발생일"
            if change == "invalid_date": target["candidate_dates"][0]["candidate_date"] = "2026-02-30"
            with self.subTest(change=change), self.assertRaises(timeline.TimelineCandidateError):
                timeline.validate_candidate_summary(bad)

    def test_missing_table_date_never_borrows_an_unrelated_following_row(self):
        for close in ("</TR>", "</tr>", "</ tr >"):
            with self.subTest(close=close):
                raw = source(
                    "<DOCUMENT><TABLE><TR><TD>합병기일</TD><TD>미정</TD>" + close +
                    "<TR><TD>이사회결의일</TD><TD>2026-11-04</TD></TR>"
                    "<TR><TD>변경상장예정일</TD><TD>2026-11-09</TD></TR>"
                    "</TABLE></DOCUMENT>")
                result = timeline.inspect_original_zip(raw)
                self.assertEqual([(r["label"], r["candidate_date"])
                                  for r in result["candidate_dates"]],
                                 [("변경상장예정일", "2026-11-09")])
                self.assertFalse(result["effective_date_authenticated"])
                timeline.validate_candidate_summary(result)

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
