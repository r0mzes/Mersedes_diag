param([string]$Port = "")
# Проверка моста ESP32: видит ли Windows порт, отвечает ли мост и адаптер ELM327.
# Запуск: powershell -ExecutionPolicy Bypass -File tools\check_esp32.ps1 [-Port COM5]

Write-Host "== USB-UART устройства ==" -ForegroundColor Cyan
$devs = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0403|VID_1A86|VID_10C4|FTDIBUS' -or $_.Class -eq 'Ports' }
if (-not $devs) {
    Write-Host "Ничего не найдено. Проверьте кабель (нужен кабель с данными, не только зарядный) и питание ESP32." -ForegroundColor Yellow
} else {
    $devs | Format-Table Status, Class, FriendlyName, InstanceId -AutoSize | Out-String -Width 200 | Write-Host
    if ($devs | Where-Object { $_.Status -ne 'OK' }) {
        Write-Host "Есть устройство с ошибкой: нужен драйвер (FTDI: https://ftdichip.com/drivers/vcp-drivers/ , CH340: драйвер CH341SER, CP210x: Silicon Labs)." -ForegroundColor Yellow
    }
}

$ports = [System.IO.Ports.SerialPort]::GetPortNames()
Write-Host "== COM-порты: $($ports -join ', ') ==" -ForegroundColor Cyan
if (-not $Port) {
    if ($ports.Count -eq 1) { $Port = $ports[0] } else { Write-Host "Укажите порт: -Port COMx"; exit 1 }
}

Write-Host "== Опрос $Port на 38400 ==" -ForegroundColor Cyan
$sp = New-Object System.IO.Ports.SerialPort $Port, 38400
$sp.DtrEnable = $false; $sp.RtsEnable = $false
try { $sp.Open() } catch { Write-Host "Порт не открывается: $($_.Exception.Message). Закройте Arduino IDE / другие программы." -ForegroundColor Red; exit 1 }
function Drain($ms) {
    $out = ""; $end = (Get-Date).AddMilliseconds($ms)
    while ((Get-Date) -lt $end) { $out += $sp.ReadExisting(); Start-Sleep -Milliseconds 100 }
    Write-Host $out; return $out
}
Drain 2000 | Out-Null
$sp.Write("~STATE`n"); $state = Drain 1500
if ($state -notmatch '~STATE') {
    Write-Host "Мост не ответил. Прошита ли ESP32 скетчем firmware\esp32_obd_bridge? TX/RX не перепутаны?" -ForegroundColor Red
} elseif ($state -match 'connected=0') {
    Write-Host "Мост работает, но адаптер не подключён. Адаптер в OBD, зажигание включено, телефон отключён от адаптера? Ищу 25 с..." -ForegroundColor Yellow
    $sp.Write("~USE 01:2d:a1:86:68:c0`n"); Drain 25000 | Out-Null
}
foreach ($c in "ATZ", "ATI", "AT RV") { $sp.Write("$c`r"); Drain 2500 | Out-Null }
$sp.Close()
Write-Host "Если выше есть 'ELM327 v1.5' и напряжение — можно запускать: python -m vito_diag scan --port $Port --baudrate 38400" -ForegroundColor Green
