// Перенесено с домашнего ПК (папка Mersedes-fiag). Похожая утилита в репозитории — firmware/hc06_slave.
// Разовая утилита: вернуть HC-06 (linvor) в режим ведомого, не снимая его с D0/D1.
// Uno сама шлёт AT-команды в HC-06 и повторяет его ответы с префиксом "# " — их видно
// в мониторе порта COM4 (9600). Перебирает скорости, на которых мог остаться модуль.
// После работы вернуть прошивку obd_switch.

const long BAUDS[] = {9600, 38400, 57600, 115200, 19200, 4800, 2400, 1200};

String ask(const char *cmd, unsigned long waitMs) {
  while (Serial.available()) Serial.read();
  Serial.print(cmd);  // linvor: команды без \r\n
  Serial.flush();
  String r;
  unsigned long t = millis();
  while (millis() - t < waitMs) {
    while (Serial.available()) r += (char)Serial.read();
  }
  return r;
}

void report(long baud, const char *cmd, const String &r) {
  Serial.begin(9600);
  Serial.print(F("# ")); Serial.print(baud); Serial.print(' ');
  Serial.print(cmd); Serial.print(F(" -> [")); Serial.print(r); Serial.println(']');
  Serial.flush();
}

void setup() {
  delay(2000);
  long found = 0;
  for (long b : BAUDS) {
    Serial.begin(b);
    String r = ask("AT", 1200);
    report(b, "AT", r);
    if (r.indexOf("OK") >= 0) { found = b; break; }
  }
  if (!found) { Serial.begin(9600); Serial.println(F("# HC-06 не отвечает")); return; }

  const char *cmds[] = {"AT+VERSION", "AT+ROLE=S", "AT+ROLE=S", "AT+BAUD4"};
  for (const char *c : cmds) {
    Serial.begin(found);
    String r = ask(c, 1500);
    report(found, c, r);
  }
  Serial.begin(9600);
  Serial.println(F("# готово: выключите питание HC-06/Uno на 5 с"));
}

void loop() {}
