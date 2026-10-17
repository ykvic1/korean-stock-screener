# Read-only local Windows PowerShell; registered IP required for -Live.
# Never writes credentials, tokens, investor quantities or account data.
[CmdletBinding()]
param(
    [switch]$Live,
    [switch]$OfflineSelfTest,
    [ValidateSet('real','demo')][string]$Mode='real',
    [ValidatePattern('^[0-9]{6}$')][string]$Code='252990',
    [ValidatePattern('^[0-9]{8}$')][string]$AsOf='20261016',
    [ValidateRange(1,3)][int]$MaxPages=3
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function Parse-Day([object]$Text) {
    if ($Text -isnot [string] -or $Text -cnotmatch '^[0-9]{8}$') { throw 'bad_date' }
    $day=[datetime]::MinValue
    $ok=[datetime]::TryParseExact($Text,'yyyyMMdd',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::None,[ref]$day)
    if (-not $ok) { throw 'bad_date' }
    return $day
}
function Share-OK([object]$Qty) {
    return ($Qty -is [string] -and $Qty -cmatch '^[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$')
}
function Read-Secret([Security.SecureString]$Value) {
    $ptr=[IntPtr]::Zero
    try {
        $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    }
}
function Audit-Pages {
    param([string]$Key,[string]$Secret,[string]$Kind,[string]$Symbol,
          [string]$Day,[int]$Limit,[scriptblock]$Post)
    if ($Kind -cnotin @('real','demo') -or $Symbol -cnotmatch '^[0-9]{6}$' -or
        $Limit -lt 1 -or $Limit -gt 3) { throw 'invalid_request' }
    $cutoff=Parse-Day $Day
    if ($cutoff.Date -gt [datetime]::Today -or
        [string]::IsNullOrWhiteSpace($Key) -or [string]::IsNullOrWhiteSpace($Secret)) {
        throw 'bad_credentials_or_asof'
    }
    $hostUrl=if ($Kind -ceq 'real') { 'https://api.kiwoom.com' } else { 'https://mockapi.kiwoom.com' }
    $auth= & $Post "$hostUrl/oauth2/token" @{
        grant_type='client_credentials';appkey=$Key;secretkey=$Secret
    } @{'Content-Type'='application/json;charset=UTF-8'}
    if ($null -eq $auth -or $null -eq $auth.body) { throw 'bad_oauth' }
    $status=$auth.body.PSObject.Properties['return_code']
    if ($null -ne $status -and [string]$status.Value -cne '0') { throw 'oauth_denied' }
    $bearer=$auth.body.token
    if ($bearer -isnot [string] -or [string]::IsNullOrWhiteSpace($bearer)) { throw 'token_missing' }
    $requestBody=@{dt=$Day;stk_cd=$Symbol;amt_qty_tp='2';trde_tp='0';unit_tp='1'}
    $baseHeaders=@{'Content-Type'='application/json;charset=UTF-8'
                   'api-id'='ka10059';authorization=('Bearer '+$bearer)}
    $previous=$null
    $first=$null
    $total=0
    $pension=0
    $pages=0
    $pageSizes=@()
    $nextKey=''
    $nextFlag=''
    $hasMore=$false
    for ($page=0;$page -lt $Limit;$page++) {
        $headers=$baseHeaders.Clone()
        if ($page -gt 0) {
            if ($nextFlag -cne 'Y' -or [string]::IsNullOrWhiteSpace($nextKey)) { throw 'continuation_missing' }
            $headers['cont-yn']='Y'
            $headers['next-key']=$nextKey
        }
        $answer= & $Post "$hostUrl/api/dostk/stkinfo" $requestBody $headers
        if ($null -eq $answer -or $null -eq $answer.body -or
            [string]$answer.body.return_code -cne '0') { throw 'flow_failed' }
        if ($null -eq $answer.body.stk_invsr_orgn) { throw 'rows_missing' }
        $rows=@($answer.body.stk_invsr_orgn)
        if ($rows.Count -lt 1 -or $rows.Count -gt 250) { throw 'invalid_page_size' }
        foreach ($row in $rows) {
            if ($null -eq $row) { throw 'missing_row' }
            $value=Parse-Day $row.dt
            if ($value -gt $cutoff -or ($null -ne $previous -and $value -ge $previous)) {
                throw 'duplicate_future_or_unordered_date'
            }
            if (-not (Share-OK $row.orgn) -or -not (Share-OK $row.frgnr_invsr)) {
                throw 'invalid_institution_or_foreign_shares'
            }
            $pensionField=$row.PSObject.Properties['penfnd_etc']
            if ($null -ne $pensionField) {
                if (-not (Share-OK $pensionField.Value)) { throw 'invalid_pension_shares' }
                $pension++
            }
            if ($null -eq $first) { $first=$value }
            $previous=$value
            $total++
        }
        $pageSizes+= $rows.Count
        $pages++
        $nextFlag=[string]$answer.cont_yn
        $nextKey=[string]$answer.next_key
        if ($nextFlag -notin @('','N','Y')) { throw 'invalid_header_continuation' }
        $hasMore=($nextFlag -ceq 'Y')
        if ($hasMore -and [string]::IsNullOrWhiteSpace($nextKey)) { throw 'missing_next_key' }
        if (-not $hasMore) {
            if (-not [string]::IsNullOrWhiteSpace($nextKey)) { throw 'unexpected_next_key' }
            break
        }
    }
    if ($pension -ne 0 -and $pension -ne $total) { throw 'partial_pension_history' }
    return [ordered]@{
        status='readonly_pagination_probe_passed'
        api_id='ka10059'
        mode=$Kind
        observed_pages=$pages
        rows_by_page=$pageSizes
        unique_ordered_date_records=$total
        newest_date=$first.ToString('yyyy-MM-dd')
        oldest_date=$previous.ToString('yyyy-MM-dd')
        pension_etc_field_complete=($pension -eq $total)
        observed_20_records=($total -ge 20)
        observed_60_records=($total -ge 60)
        observed_120_records=($total -ge 120)
        observed_250_records=($total -ge 250)
        more_pages_at_limit=$hasMore
        cross_source_numeric_match_verified=$false
        independent_trading_calendar_verified=$false
        krx_session_and_subgroup_definitions_verified=$false
        automated_reuse_permitted_verified=$false
        confirmed_scoring_allowed=$false
        ias_flow_component=$null
        v3_release_ready=$false
    }
}
if ($OfflineSelfTest) {
    if ($Live) { Write-Output 'pagination_offline_self_test_failed';exit 1 }
    try {
        $script:mockDays=@()
        $testDay=[datetime]::ParseExact('20261016','yyyyMMdd',[Globalization.CultureInfo]::InvariantCulture)
        while ($script:mockDays.Count -lt 260) {
            if ($testDay.DayOfWeek -notin @([DayOfWeek]::Saturday,[DayOfWeek]::Sunday)) {
                $script:mockDays+= $testDay.ToString('yyyyMMdd')
            }
            $testDay=$testDay.AddDays(-1)
        }
        $script:mockPage=0
        $mock={
            param($Uri,$Payload,$Headers)
            if ($Uri -like '*/oauth2/token') {
                return [pscustomobject]@{body=[pscustomobject]@{token='SYNTHETIC_TOKEN'};cont_yn='';next_key=''}
            }
            $index=$script:mockPage
            if ($Headers['api-id'] -cne 'ka10059' -or $Payload.amt_qty_tp -cne '2' -or
                $Payload.trde_tp -cne '0' -or $Payload.unit_tp -cne '1') { throw 'wrong_request' }
            if ($index -eq 0 -and ($Headers.ContainsKey('cont-yn') -or $Headers.ContainsKey('next-key'))) {
                throw 'unexpected_first_cursor'
            }
            if ($index -gt 0 -and ($Headers['cont-yn'] -cne 'Y' -or
                $Headers['next-key'] -cne ('cursor'+$index))) { throw 'wrong_cursor' }
            $raw=@()
            for ($i=$index*100;$i -lt [math]::Min(($index+1)*100,260);$i++) {
                $raw+= [pscustomobject]@{dt=$script:mockDays[$i];orgn='-100';frgnr_invsr='+200';penfnd_etc='0'}
            }
            $script:mockPage++
            return [pscustomobject]@{
                body=[pscustomobject]@{return_code=0;stk_invsr_orgn=$raw}
                cont_yn=$(if($index -lt 2){'Y'}else{'N'})
                next_key=$(if($index -lt 2){'cursor'+($index+1)}else{''})
            }
        }
        $a=Audit-Pages 'FAKE_KEY' 'FAKE_SECRET' 'real' '252990' '20261016' 3 $mock
        if ($a.observed_pages -ne 3 -or $a.unique_ordered_date_records -ne 260 -or
            -not $a.observed_250_records -or $a.confirmed_scoring_allowed) {
            throw 'bad_three_page_result'
        }
        $script:mockPage=0
        $one=Audit-Pages 'FAKE_KEY' 'FAKE_SECRET' 'real' '252990' '20261016' 1 $mock
        if ($one.unique_ordered_date_records -ne 100 -or -not $one.more_pages_at_limit -or
            $one.observed_120_records) { throw 'bad_one_page_result' }
        # Duplicate at the boundary between synthetic page 1 and 2 must fail.
        $savedDay=$script:mockDays[100]
        $script:mockDays[100]=$script:mockDays[99]
        $script:mockPage=0
        $duplicateRejected=$false
        try {
            $null=Audit-Pages 'FAKE_KEY' 'FAKE_SECRET' 'real' '252990' '20261016' 2 $mock
        }
        catch { $duplicateRejected=$true }
        $script:mockDays[100]=$savedDay
        if (-not $duplicateRejected) { throw 'duplicate_across_pages_accepted' }
        # Missing continuation cursor cannot silently truncate 250-day evidence.
        $missingCursor={
            param($Uri,$Payload,$Headers)
            if ($Uri -like '*/oauth2/token') {
                return [pscustomobject]@{body=[pscustomobject]@{token='FAKE_TOKEN'};cont_yn='';next_key=''}
            }
            return [pscustomobject]@{
                body=[pscustomobject]@{
                    return_code=0
                    stk_invsr_orgn=@([pscustomobject]@{
                        dt='20261016';orgn='0';frgnr_invsr='1'
                    })
                }
                cont_yn='Y'
                next_key=''
            }
        }
        $cursorRejected=$false
        try {
            $null=Audit-Pages 'FAKE_KEY' 'FAKE_SECRET' 'real' '252990' '20261016' 2 $missingCursor
        }
        catch { $cursorRejected=$true }
        if (-not $cursorRejected) { throw 'missing_cursor_accepted' }
        # Amount-like decimal strings are not allowed signed share quantities.
        $badQuantity={
            param($Uri,$Payload,$Headers)
            if ($Uri -like '*/oauth2/token') {
                return [pscustomobject]@{body=[pscustomobject]@{token='FAKE_TOKEN'};cont_yn='';next_key=''}
            }
            return [pscustomobject]@{
                body=[pscustomobject]@{
                    return_code=0
                    stk_invsr_orgn=@([pscustomobject]@{
                        dt='20261016';orgn='1.5';frgnr_invsr='0'
                    })
                }
                cont_yn='N'
                next_key=''
            }
        }
        $decimalRejected=$false
        try {
            $null=Audit-Pages 'FAKE_KEY' 'FAKE_SECRET' 'real' '252990' '20261016' 1 $badQuantity
        }
        catch { $decimalRejected=$true }
        if (-not $decimalRejected) { throw 'decimal_quantity_accepted' }
        Write-Output 'pagination_offline_self_test_passed'
        exit 0
    }
    catch {
        Write-Output 'pagination_offline_self_test_failed'
        exit 1
    }
}
if (-not $Live) {
    Write-Output 'pagination_not_run: registered_IP_and_Live_switch_required'
    exit 2
}
$key=$null
$secret=$null
$protectedKey=$null
$protectedSecret=$null
try {
    $protectedKey=Read-Host 'Kiwoom App Key (hidden)' -AsSecureString
    $protectedSecret=Read-Host 'Kiwoom App Secret (hidden)' -AsSecureString
    $key=Read-Secret $protectedKey
    $secret=Read-Secret $protectedSecret
    $post={
        param($Uri,$Payload,$Headers)
        $encoded=[Text.Encoding]::UTF8.GetBytes(
            (ConvertTo-Json -InputObject $Payload -Depth 4 -Compress))
        $http=Invoke-WebRequest -UseBasicParsing -Uri $Uri -Method Post -Body $encoded -ContentType 'application/json;charset=UTF-8' -Headers $Headers -TimeoutSec 15 -ErrorAction Stop
        return [pscustomobject]@{
            body=(ConvertFrom-Json -InputObject $http.Content)
            cont_yn=([string]$http.Headers['cont-yn'])
            next_key=([string]$http.Headers['next-key'])
        }
    }
    $report=Audit-Pages $key $secret $Mode $Code $AsOf $MaxPages $post
    Write-Output (ConvertTo-Json -InputObject $report -Depth 4 -Compress)
    exit 0
}
catch {
    # Never display an HTTP error body, exception, token, secret, stock quantities or IP.
    Write-Output 'pagination_probe_failed: check_registered_IP_mode_or_continuation'
    exit 1
}
finally {
    $key=$null
    $secret=$null
    $protectedKey=$null
    $protectedSecret=$null
}
