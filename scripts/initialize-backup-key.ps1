[CmdletBinding(SupportsShouldProcess)]
param([string]$KeyPath='C:\ProgramData\Gbuzz\secrets\backup-key.dpapi')
$ErrorActionPreference='Stop'
if(Test-Path $KeyPath){throw 'A backup key already exists; refusing to replace it.'}
$secure=Read-Host 'Enter the owner-vaulted 64-byte backup key in base64' -AsSecureString
$bstr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try{$raw=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr);try{$key=[Convert]::FromBase64String($raw)}catch{throw 'Key is not valid base64.'};if($key.Length-ne 64){throw 'Key must decode to exactly 64 bytes.'};$protected=[Security.Cryptography.ProtectedData]::Protect($key,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);if($PSCmdlet.ShouldProcess($KeyPath,'Store a CurrentUser-DPAPI protected backup key')){$directory=Split-Path -Parent $KeyPath;New-Item -ItemType Directory -Force $directory|Out-Null;[IO.File]::WriteAllBytes($KeyPath,$protected);$acl=Get-Acl $KeyPath;$acl.SetAccessRuleProtection($true,$false);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.WindowsIdentity]::GetCurrent().Name,'FullControl','Allow'));Set-Acl -LiteralPath $KeyPath -AclObject $acl}}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr);if($key){[Array]::Clear($key,0,$key.Length)};if($raw){$raw=$null}}
