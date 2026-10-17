# Kiwoom REST manual Windows PowerShell 5.1/7 probe; no Python required.
# The -Live mode performs precisely 1 OAuth POST and 1 ka10059 POST.
# No orders, portfolio access, IP checking service, stored credentials or raw flow output.
[CmdletBinding()]
param(
    [switch]$Live,
    [switch]$OfflineSelfTest,
    [ValidateSet('real','demo')][string]$Mode = 'real',
    [ValidatePattern('^[0-9]{6}$')][string]$Code = '252990',
    [ValidatePattern('^[0-9]{8}$')][string]$AsOf = '20261016'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-HiddenPlainText {
    param([System.Security.SecureString]$Secure)
    $ptr = [IntPtr]::Zero
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}

function Convert-TradeDate {
    param([object]$Value)
    if ($Value -isnot [string] -or $Value -cnotmatch '^[0-9]{8}$') {
        throw 'invalid_trade_date'
    }
    $day = [datetime]::MinValue
    $valid = [datetime]::TryParseExact($Value,'yyyyMMdd',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::None,[ref]$day)
    if (-not $valid) { throw 'invalid_trade_date' }
    return $day
}

function Test-ShareInteger {
    param([object]$Value)
    return ($Value -is [string] -and $Value -cmatch '^[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$')
}

function Invoke-ReadOnlyKiwoom {
    param(
        [string]$AppKey,
        [string]$AppSecret,
        [string]$ApiMode,
        [string]$Stock,
        [string]$DateText,
        [scriptblock]$Post
    )
    if ($ApiMode -cnotin @('real','demo')) { throw 'invalid_api_mode' }
    if ($Stock -cnotmatch '^[0-9]{6}$') { throw 'invalid_stock' }
    $cutoff = Convert-TradeDate $DateText
    if ($cutoff.Date -gt [datetime]::Today) { throw 'future_date' }
    if ([string]::IsNullOrWhiteSpace($AppKey) -or [string]::IsNullOrWhiteSpace($AppSecret)) {
        throw 'missing_credentials'
    }
    $hostName = if ($ApiMode -ceq 'real') { 'https://api.kiwoom.com' } else { 'https://mockapi.kiwoom.com' }

    $token = & $Post "$hostName/oauth2/token" @{grant_type='client_credentials';appkey=$AppKey;secretkey=$AppSecret} @{'Content-Type'='application/json;charset=UTF-8'}
    if ($null -eq $token) { throw 'invalid_token_response' }
    $tokenCode = $token.PSObject.Properties['return_code']
    if ($null -ne $tokenCode -and [string]$tokenCode.Value -cne '0') { throw 'token_denied' }
    if ($token.token -isnot [string] -or [string]::IsNullOrWhiteSpace($token.token)) { throw 'missing_access_token' }

    $body = @{dt=$DateText;stk_cd=$Stock;amt_qty_tp='2';trde_tp='0';unit_tp='1'}
    $headers = @{'Content-Type'='application/json;charset=UTF-8';'api-id'='ka10059';authorization=('Bearer '+$token.token)}
    $response = & $Post "$hostName/api/dostk/stkinfo" $body $headers
    if ($null -eq $response -or [string]$response.return_code -cne '0') { throw 'flow_denied' }
    if ($null -eq $response.stk_invsr_orgn) { throw 'missing_rows' }

    $rows = @($response.stk_invsr_orgn)
    if ($rows.Count -lt 1 -or $rows.Count -gt 250) { throw 'invalid_row_count' }
    $previous = $null
    $pensionCount = 0
    $first = $null
    $last = $null
    foreach ($row in $rows) {
        if ($null -eq $row) { throw 'invalid_record' }
        $day = Convert-TradeDate $row.dt
        if ($day -gt $cutoff -or ($null -ne $previous -and $day -ge $previous)) { throw 'invalid_date_sequence' }
        if (-not (Test-ShareInteger $row.orgn) -or -not (Test-ShareInteger $row.frgnr_invsr)) {
            throw 'invalid_signed_shares'
        }
        $pensionField = $row.PSObject.Properties['penfnd_etc']
        if ($null -ne $pensionField) {
            if (-not (Test-ShareInteger $pensionField.Value)) { throw 'invalid_pension_etc' }
            $pensionCount++
        }
        if ($null -eq $first) { $first = $day }
        $last = $day
        $previous = $day
    }
    if ($pensionCount -ne 0 -and $pensionCount -ne $rows.Count) { throw 'partial_pension_etc' }
    return [ordered]@{
        status='readonly_probe_passed'
        mode=$ApiMode
        api_id='ka10059'
        received_rows_one_page=$rows.Count
        newest_date=$first.ToString('yyyy-MM-dd')
        oldest_date=$last.ToString('yyyy-MM-dd')
        investor_quantity_format_valid=$true
        pension_etc_field_complete=($pensionCount -eq $rows.Count)
        independent_source_verified=$false
        krx_regular_session_verified=$false
        historical_20_60_120_250_verified=$false
        ias_flow_component=$null
        confirmed_scoring_allowed=$false
        v3_release_ready=$false
    }
}

if ($OfflineSelfTest) {
    if ($Live) { Write-Output 'offline_self_test_failed'; exit 1 }
    try {
        $mockPost = {
            param($Uri,$Payload,$Headers)
            if ($Uri -like '*/oauth2/token') {
                if ($Payload.grant_type -cne 'client_credentials') { throw 'bad_token_request' }
                return [pscustomobject]@{token='SYNTHETIC_TOKEN';return_code=0}
            }
            if ($Uri -notlike '*/api/dostk/stkinfo' -or $Headers['api-id'] -cne 'ka10059' -or $Payload.amt_qty_tp -cne '2' -or $Payload.trde_tp -cne '0' -or $Payload.unit_tp -cne '1') {
                throw 'bad_flow_request'
            }
            return [pscustomobject]@{
                return_code=0
                stk_invsr_orgn=@(
                    [pscustomobject]@{dt='20261016';orgn='-120';frgnr_invsr='+75';penfnd_etc='0'},
                    [pscustomobject]@{dt='20261015';orgn='+30';frgnr_invsr='-20';penfnd_etc='-2'}
                )
            }
        }
        $result = Invoke-ReadOnlyKiwoom -AppKey 'SYNTHETIC_KEY' -AppSecret 'SYNTHETIC_SECRET' -ApiMode 'real' -Stock '252990' -DateText '20261016' -Post $mockPost
        if ($result.status -cne 'readonly_probe_passed' -or $result.received_rows_one_page -ne 2 -or $result.confirmed_scoring_allowed -ne $false) {
            throw 'bad_result'
        }
        $badPost = {
            param($Uri,$Payload,$Headers)
            if ($Uri -like '*/oauth2/token') { return [pscustomobject]@{token='SYNTHETIC_TOKEN'} }
            return [pscustomobject]@{return_code=0;stk_invsr_orgn=@([pscustomobject]@{dt='20261016';orgn='1.5';frgnr_invsr='0'})}
        }
        $rejected = $false
        try {
            $null = Invoke-ReadOnlyKiwoom -AppKey 'SYNTHETIC_KEY' -AppSecret 'SYNTHETIC_SECRET' -ApiMode 'real' -Stock '252990' -DateText '20261016' -Post $badPost
        }
        catch { $rejected = $true }
        if (-not $rejected) { throw 'bad_quantity_accepted' }
        Write-Output 'offline_self_test_passed'
        exit 0
    }
    catch { Write-Output 'offline_self_test_failed'; exit 1 }
}

if (-not $Live) {
    Write-Output 'not_run: registered_public_IP_and_Live_switch_required'
    exit 2
}

# Only run on a Kiwoom-registered public IP. Read two secrets from hidden prompts.
$appKey = $null
$appSecret = $null
try {
    $secureKey = Read-Host 'Kiwoom App Key (hidden)' -AsSecureString
    $secureSecret = Read-Host 'Kiwoom App Secret (hidden)' -AsSecureString
    $appKey = Read-HiddenPlainText $secureKey
    $appSecret = Read-HiddenPlainText $secureSecret
    $postRequest = {
        param($Uri,$Payload,$Headers)
        $utf8 = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Payload -Compress -Depth 4))
        return Invoke-RestMethod -Uri $Uri -Method Post -Body $utf8 -ContentType 'application/json;charset=UTF-8' -Headers $Headers -TimeoutSec 15 -ErrorAction Stop
    }
    $report = Invoke-ReadOnlyKiwoom -AppKey $appKey -AppSecret $appSecret -ApiMode $Mode -Stock $Code -DateText $AsOf -Post $postRequest
    Write-Output (ConvertTo-Json -InputObject $report -Compress)
    exit 0
}
catch {
    # Never output exception detail, remote HTTP body, secrets or access tokens.
    Write-Output 'probe_failed: check_registered_IP_mode_date_or_API_access'
    exit 1
}
finally {
    $appKey = $null
    $appSecret = $null
    $secureKey = $null
    $secureSecret = $null
}
