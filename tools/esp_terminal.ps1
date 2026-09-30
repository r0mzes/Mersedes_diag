param([string[]]$Lines = @(), [switch]$Reset, [int]$ListenMs = 3000, [string]$Port = "COM5")
# Строки "wait:N" ждут N мс; остальные отправляются с \r (строки '~' — с \n). Печатает всё, что пришло.
$sp = New-Object System.IO.Ports.SerialPort $Port, 38400
$sp.DtrEnable = $false; $sp.RtsEnable = $false
$sp.Open()
if ($Reset) { $sp.RtsEnable = $true; Start-Sleep -Milliseconds 150; $sp.RtsEnable = $false }
function Drain($ms) {
    $end = (Get-Date).AddMilliseconds($ms)
    while ((Get-Date) -lt $end) { $r = $sp.ReadExisting(); if ($r) { [Console]::Out.Write($r) }; Start-Sleep -Milliseconds 100 }
}
Drain $ListenMs
foreach ($l in $Lines) {
    if ($l -match '^wait:(\d+)$') { Drain ([int]$Matches[1]); continue }
    $e = if ($l.StartsWith('~')) { "`n" } else { "`r" }
    [Console]::Out.WriteLine(">> $l")
    $sp.Write("$l$e")
    Drain 2500
}
$sp.Close()
