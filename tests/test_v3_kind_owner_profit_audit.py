import unittest

from v3_kind_owner_profit_audit import audit


class KindOwnerProfitAuditTest(unittest.TestCase):
    def test_receipt_anchor_checks_use_the_current_annual_column(self):
        # Minimal rows transcribed from the public KIND annual filing.
        body = '''
        <p>디와이파워 2025.01.01 2025.12.31</p>
        <p>연결 포괄손익계산서</p>
        <table>
          <tr><td>Ⅰ.수익(매출액)</td><td>382,750,873,929</td></tr>
          <tr><td>Ⅴ.영업이익(손실)</td><td>30,429,405,841</td></tr>
          <tr><td>XⅢ.당기순이익(손실)</td><td>25,143,819,961</td></tr>
        </table>
        <p>연결 현금흐름표</p>
        <table><tr><td>Ⅰ.영업활동현금흐름</td><td>23,102,288,824</td></tr></table>
        <p>34. 주당이익 (연결)</p>
        <table>
          <tr><td>지배주주 당기순이익</td><td>25,143,819,961</td></tr>
          <tr><td>가중평균유통보통주식수</td><td>11,035,851</td></tr>
          <tr><td>기본주당이익</td><td>2,278</td></tr>
        </table>
        '''.encode('utf-8')
        anchors = {'revenue': 382750873929, 'operating_income': 30429405841,
                   'cfo': 23102288824}
        self.assertEqual(audit(body, anchors, 2278), 25143819961)
        for field, value in anchors.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                audit(body, {**anchors, field: value + 1}, 2278)
        with self.assertRaises(ValueError):
            audit(body, anchors, 1987)
