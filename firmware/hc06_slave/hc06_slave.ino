// Разовая утилита: вернуть HC-06 (на D0/D1 Uno) в режим ведомого (slave), чтобы его снова
// было видно по Bluetooth. Нужна, если модуль когда-то перевели в master (AT+ROLE=M).
//
// HC-06 принимает AT-команды, только пока к нему никто не подключён. Скетч перебирает скорости,
// на каждой шлёт «AT» (без и с CR/LF); где модуль ответил OK — шлёт AT+VERSION и AT+ROLE=S.
// Итог печатается раз в 2 с на 9600 строками, начинающимися с '#': откройте COM-порт Uno на 9600
// (открытие порта перезагружает Uno, и перебор пойдёт заново — это нормально, ~20 с).
// После «ROLE=S» выключите питание Uno на пару секунд и залейте обратно firmware/obd_switch.
// Заливать с СНЯТЫМ HC-06 (он на D0 мешает заливке), потом поставить его обратно.

const long BAUDS[] = {9600, 38400, 115200, 57600, 19200, 4800, 2400, 1200};
String report;

String ask(const char *cmd, bool crlf, uint16_t waitMs) {
  while (Serial.available()) Serial.read();
  Serial.print(cmd);
  if (crlf) Serial.print("\r\n");
  Serial.flush();
  String r;
  for (uint32_t t0 = millis(); millis() - t0 < waitMs;) {
    while (Serial.available()) {
      char ch = Serial.read();
      if (ch >= 32 && ch < 127 && r.length() < 60) r += ch;
    }
  }
  return r;
}

void setup() {
  delay(1500);  // HC-06 успевает стартовать
  for (long b : BAUDS) {
    Serial.begin(b);
    delay(100);
    bool crlf = false;
    String r = ask("AT", false, 1200);
    if (r.indexOf("OK") < 0) { crlf = true; r = ask("AT", true, 1200); }
    if (r.indexOf("OK") < 0) { Serial.end(); continue; }
    report = "# baud " + String(b) + (crlf ? " crlf" : "") + " AT=" + r;
    report += " | VERSION=" + ask("AT+VERSION", crlf, 1500);
    report += " | ROLE=S -> " + ask("AT+ROLE=S", crlf, 2000);
    Serial.end();
    break;
  }
  if (!report.length()) report = "# no answer at any baud (connected? wiring? already slave?)";
  Serial.begin(9600);
}

void loop() {
  Serial.println(report);
  delay(2000);
}
