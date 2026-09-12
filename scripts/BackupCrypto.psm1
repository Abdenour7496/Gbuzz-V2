Set-StrictMode -Version Latest

function Get-BackupKey {
    $raw=$env:GBUZZ_BACKUP_KEY_BASE64
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $path=if($env:GBUZZ_BACKUP_KEY_FILE){$env:GBUZZ_BACKUP_KEY_FILE}else{'C:\ProgramData\Gbuzz\secrets\backup-key.dpapi'}
        if(-not(Test-Path $path)){throw 'Backup key is not initialized for this operating identity.'}
        try{$protected=[IO.File]::ReadAllBytes($path);$key=[Security.Cryptography.ProtectedData]::Unprotect($protected,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)}catch{throw 'Backup key could not be unlocked by this operating identity.'}
        if($key.Length-ne 64){throw 'Unlocked backup key is invalid.'};return ,$key
    }
    try { $key=[Convert]::FromBase64String($raw) } catch { throw 'GBUZZ_BACKUP_KEY_BASE64 is not valid base64.' }
    if ($key.Length -ne 64) { throw 'GBUZZ_BACKUP_KEY_BASE64 must decode to exactly 64 bytes.' }
    return ,$key
}

function ConvertTo-Hex([byte[]]$Bytes){([BitConverter]::ToString($Bytes)-replace '-','').ToLowerInvariant()}
function Test-FixedTimeEqual([byte[]]$Left,[byte[]]$Right){if($Left.Length-ne $Right.Length){return $false};$difference=0;for($i=0;$i-lt $Left.Length;$i++){$difference=$difference-bor($Left[$i]-bxor$Right[$i])};return $difference-eq 0}
function Get-FileSha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

function Protect-BackupFile([string]$InputPath,[string]$OutputPath,[byte[]]$Key) {
    $magic=[Text.Encoding]::ASCII.GetBytes('GBUZZB1')
    $iv=New-Object byte[] 16
    $rng=[Security.Cryptography.RandomNumberGenerator]::Create();try{$rng.GetBytes($iv)}finally{$rng.Dispose()}
    $cipher="$OutputPath.cipher"
    try {
        $aes=[Security.Cryptography.Aes]::Create();$aes.Key=[byte[]]$Key[0..31];$aes.IV=$iv;$aes.Mode='CBC';$aes.Padding='PKCS7'
        $source=[IO.File]::OpenRead($InputPath);$target=[IO.File]::Create($cipher)
        try { $crypto=[Security.Cryptography.CryptoStream]::new($target,$aes.CreateEncryptor(),'Write');try{$source.CopyTo($crypto)}finally{$crypto.Dispose()} } finally {$source.Dispose();$target.Dispose();$aes.Dispose()}
        $out=[IO.File]::Create($OutputPath);try{$out.Write($magic,0,$magic.Length);$out.Write($iv,0,$iv.Length);$body=[IO.File]::OpenRead($cipher);try{$body.CopyTo($out)}finally{$body.Dispose()}}finally{$out.Dispose()}
        $hmac=[Security.Cryptography.HMACSHA256]::new([byte[]]$Key[32..63]);try{$signed=[IO.File]::OpenRead($OutputPath);try{$tag=$hmac.ComputeHash($signed)}finally{$signed.Dispose()}}finally{$hmac.Dispose()}
        $out=[IO.File]::Open($OutputPath,[IO.FileMode]::Append,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$out.Write($tag,0,$tag.Length)}finally{$out.Dispose()}
        return ConvertTo-Hex $tag
    } finally { Remove-Item -LiteralPath $cipher -Force -ErrorAction SilentlyContinue }
}

function Unprotect-BackupFile([string]$InputPath,[string]$OutputPath,[byte[]]$Key) {
    $source=[IO.File]::OpenRead($InputPath);$cipherPath="$OutputPath.cipher"
    try{
        if($source.Length -lt 56){throw 'Invalid encrypted backup format.'};$magicBytes=New-Object byte[] 7;$null=$source.Read($magicBytes,0,7);if([Text.Encoding]::ASCII.GetString($magicBytes)-ne 'GBUZZB1'){throw 'Invalid encrypted backup format.'};$iv=New-Object byte[] 16;$null=$source.Read($iv,0,16)
        $cipherLength=$source.Length-23-32;$cipherOut=[IO.File]::Create($cipherPath);try{$buffer=New-Object byte[] 1048576;$remaining=$cipherLength;while($remaining-gt 0){$read=$source.Read($buffer,0,[int][math]::Min($buffer.Length,$remaining));if($read-le 0){throw 'Truncated encrypted backup.'};$cipherOut.Write($buffer,0,$read);$remaining-=$read}}finally{$cipherOut.Dispose()};$tag=New-Object byte[] 32;$null=$source.Read($tag,0,32)
    }finally{$source.Dispose()}
    $hmac=[Security.Cryptography.HMACSHA256]::new([byte[]]$Key[32..63]);try{$headerAndCipher=[IO.File]::OpenRead($InputPath);try{$buffer=New-Object byte[] 1048576;$remaining=$headerAndCipher.Length-32;while($remaining-gt 0){$read=$headerAndCipher.Read($buffer,0,[int][math]::Min($buffer.Length,$remaining));$null=$hmac.TransformBlock($buffer,0,$read,$buffer,0);$remaining-=$read};$null=$hmac.TransformFinalBlock(@(),0,0);$expected=$hmac.Hash}finally{$headerAndCipher.Dispose()}}finally{$hmac.Dispose()}
    if(-not (Test-FixedTimeEqual $tag $expected)){Remove-Item $cipherPath -Force;throw 'Encrypted backup authentication failed.'}
    $aes=[Security.Cryptography.Aes]::Create();$aes.Key=[byte[]]$Key[0..31];$aes.IV=$iv;$aes.Mode='CBC';$aes.Padding='PKCS7'
    try{$input=[IO.File]::OpenRead($cipherPath);$crypto=[Security.Cryptography.CryptoStream]::new($input,$aes.CreateDecryptor(),'Read');$output=[IO.File]::Create($OutputPath);try{$crypto.CopyTo($output)}finally{$output.Dispose();$crypto.Dispose();$input.Dispose()}}finally{$aes.Dispose();Remove-Item $cipherPath -Force -ErrorAction SilentlyContinue}
}

function Get-ManifestMac([string]$Json,[byte[]]$Key){$h=[Security.Cryptography.HMACSHA256]::new([byte[]]$Key[32..63]);try{ConvertTo-Hex $h.ComputeHash([Text.Encoding]::UTF8.GetBytes($Json))}finally{$h.Dispose()}}

Export-ModuleMember -Function Get-BackupKey,Get-FileSha256,Protect-BackupFile,Unprotect-BackupFile,Get-ManifestMac
