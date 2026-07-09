/*
  AGV Motor Controller - Arduino Uno (PRODUCTION-GRADE VERSION)
  
  HARDWARE CONECTADO:
  - Raspberry Pi 5 → Serial USB (pinos RX/TX padrão)
  - 2x Controlador BLDC YWMJZEU
  - 2x Motores BLDC
  
  PINAGEM OBRIGATÓRIA:
  Motor 1 (Direito):
    - PWM:   Pino 5  (velocidade 0-255)
    - DIR:   Pino 4  (direção FWD/REV)
    - BRAKE: Pino 3  (frenagem)
  
  Motor 2 (Esquerdo):
    - PWM:   Pino 9  (velocidade 0-255)
    - DIR:   Pino 10 (direção FWD/REV)
    - BRAKE: Pino 11 (frenagem)
  
  PINAGEM OPCIONAL (Recomendado):
    - LED Status: Pino 13 (LED built-in do Arduino)
      └─ Piscando: Funcionando normal
      └─ Fixo:     Emergência/Erro
      └─ Apagado:  Sem comunicação
  
  MELHORIAS IMPLEMENTADAS:
  ✅ Segurança:
     - Timeout automático (500ms)
     - Proteção anti-reversão brusca
     - Freio inteligente
     - Modo degradado (limita velocidade se erro)
     
  ✅ Performance:
     - Rampa de aceleração adaptativa
     - Soft-start inteligente
     - Filtragem anti-tremor (3 amostras)
     
  ✅ Comunicação:
     - Comandos especiais: STOP, RESET, STATUS, PING
     - Telemetria completa (uptime, erros, comandos)
     - Feedback expandido
     
  ✅ Diagnóstico:
     - Contador de erros
     - Estatísticas de operação
     - Health check automático
*/

// ===== CONFIGURAÇÃO DE PINOS =====
const int motor1_pwm = 5;    // motor direito
const int motor1_dir = 4;
const int motor1_brake = 3;

const int motor2_pwm = 9;    // motor esquerdo
const int motor2_dir = 10;
const int motor2_brake = 11;

const int LED_STATUS = 13;   // LED built-in (opcional mas recomendado)

// ===== CONFIGURAÇÕES DE SEGURANÇA =====
const unsigned long TIMEOUT_MS = 500;           // Timeout de comunicação
const unsigned long FEEDBACK_INTERVAL = 100;    // Intervalo de feedback
const unsigned long LED_BLINK_INTERVAL = 500;   // Intervalo de piscar LED
const int MAX_ACCEL_STEP = 15;                  // Máximo aumento de velocidade por ciclo
const int MAX_DECEL_STEP = 30;                  // Máximo redução por ciclo (mais rápido)
const int MIN_SPEED_FOR_DIRECTION_CHANGE = 20;  // Velocidade mínima para inverter direção
const int FILTER_SAMPLES = 3;                   // Amostras para filtragem anti-tremor
const int MAX_ERRORS_BEFORE_DEGRADED = 10;      // Erros antes de modo degradado
const int DEGRADED_SPEED_LIMIT = 180;           // Limite de velocidade em modo degradado (70%)

// ===== VARIÁVEIS DE ESTADO =====
unsigned long lastCommandTime = 0;
unsigned long lastFeedbackTime = 0;
unsigned long lastLedToggle = 0;
unsigned long startTime = 0;

int motor1_current = 0;      // Velocidade atual
int motor2_current = 0;
int motor1_target = 0;       // Velocidade alvo
int motor2_target = 0;

bool motor1_dirCurrent = HIGH;
bool motor2_dirCurrent = HIGH;
bool motor1_dirTarget = HIGH;
bool motor2_dirTarget = HIGH;

bool emergencyStop = false;
bool degradedMode = false;
bool ledState = false;
char serialBuffer[32];
int bufferIndex = 0;

// ===== FILTRAGEM ANTI-TREMOR =====
int accelHistory[FILTER_SAMPLES] = {0};
int dirHistory[FILTER_SAMPLES] = {0};
int filterIndex = 0;

// ===== ESTATÍSTICAS E DIAGNÓSTICO =====
unsigned long commandCount = 0;
unsigned long errorCount = 0;
unsigned long successCount = 0;
int consecutiveErrors = 0;

// ===== SETUP =====
void setup() {
  // Configurar pinos
  pinMode(motor1_pwm, OUTPUT);
  pinMode(motor1_dir, OUTPUT);
  pinMode(motor1_brake, OUTPUT);

  pinMode(motor2_pwm, OUTPUT);
  pinMode(motor2_dir, OUTPUT);
  pinMode(motor2_brake, OUTPUT);
  
  pinMode(LED_STATUS, OUTPUT);

  // Estado inicial seguro
  stopMotors(true);
  
  Serial.begin(9600);
  Serial.setTimeout(50);
  
  // Sequência de inicialização (LED pisca 3x)
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_STATUS, HIGH);
    delay(150);
    digitalWrite(LED_STATUS, LOW);
    delay(150);
  }
  
  // Sinal de inicialização
  Serial.println("ARDUINO:READY:v2.0");
  
  startTime = millis();
  lastCommandTime = millis();
  lastFeedbackTime = millis();
  lastLedToggle = millis();
}

// ===== LOOP PRINCIPAL =====
void loop() {
  unsigned long currentTime = millis();
  
  // *** VERIFICAÇÃO DE TIMEOUT ***
  if (currentTime - lastCommandTime > TIMEOUT_MS) {
    if (!emergencyStop) {
      emergencyStop = true;
      motor1_target = 0;
      motor2_target = 0;
      digitalWrite(LED_STATUS, HIGH); // LED fixo em emergência
      Serial.println("ARDUINO:TIMEOUT");
      errorCount++;
    }
  }
  
  // *** LEITURA DE COMANDOS SERIAL ***
  while (Serial.available() > 0) {
    char c = Serial.read();
    
    if (c == '\n' || c == '\r') {
      if (bufferIndex > 0) {
        serialBuffer[bufferIndex] = '\0';
        processCommand(serialBuffer);
        bufferIndex = 0;
      }
    } else if (bufferIndex < sizeof(serialBuffer) - 1) {
      serialBuffer[bufferIndex++] = c;
    } else {
      // Buffer overflow - descartar
      bufferIndex = 0;
      errorCount++;
      consecutiveErrors++;
      Serial.println("ARDUINO:ERROR:OVERFLOW");
    }
  }
  
  // *** APLICAR RAMPA DE ACELERAÇÃO ***
  applySmoothing();
  
  // *** ATUALIZAR MOTORES ***
  updateMotors();
  
  // *** LED DE STATUS ***
  updateStatusLED(currentTime);
  
  // *** FEEDBACK PERIÓDICO ***
  if (currentTime - lastFeedbackTime > FEEDBACK_INTERVAL) {
    sendFeedback();
    lastFeedbackTime = currentTime;
  }
  
  delay(10); // Loop de 100Hz
}

// ===== PROCESSAR COMANDO =====
void processCommand(char* cmd) {
  // Comandos especiais
  if (strcmp(cmd, "STOP") == 0) {
    emergencyStop = true;
    motor1_target = 0;
    motor2_target = 0;
    Serial.println("ARDUINO:STOPPED");
    return;
  }
  
  if (strcmp(cmd, "RESET") == 0) {
    resetSystem();
    Serial.println("ARDUINO:RESET:OK");
    return;
  }
  
  if (strcmp(cmd, "STATUS") == 0) {
    sendDetailedStatus();
    return;
  }
  
  if (strcmp(cmd, "PING") == 0) {
    Serial.println("ARDUINO:PONG");
    lastCommandTime = millis();
    return;
  }
  
  // Formato esperado: "aceleracao,direcao" ex: "150,50"
  int aceleracao, direcao;
  if (sscanf(cmd, "%d,%d", &aceleracao, &direcao) != 2) {
    Serial.println("ARDUINO:ERROR:INVALID");
    errorCount++;
    consecutiveErrors++;
    checkDegradedMode();
    return;
  }
  
  // Validar limites
  aceleracao = constrain(aceleracao, -255, 255);
  direcao = constrain(direcao, -254, 254);
  
  // Aplicar filtro anti-tremor
  aceleracao = applyFilter(aceleracao, accelHistory);
  direcao = applyFilter(direcao, dirHistory);
  
  // Modo degradado limita velocidade
  if (degradedMode) {
    int magnitude = abs(aceleracao);
    int degradedLimit = min(magnitude, DEGRADED_SPEED_LIMIT);
    aceleracao = aceleracao < 0 ? -degradedLimit : degradedLimit;
  }
  
  // Resetar timeout e emergência
  lastCommandTime = millis();
  emergencyStop = false;
  commandCount++;
  successCount++;
  consecutiveErrors = 0; // Reset contador de erros consecutivos
  
  // Calcular potências alvo
  calculateMotorTargets(aceleracao, direcao);
  
  // Confirmar recebimento
  Serial.print("ARDUINO:OK:");
  Serial.print(aceleracao);
  Serial.print(",");
  Serial.println(direcao);
}

// ===== CALCULAR POTÊNCIAS ALVO =====
void calculateMotorTargets(int aceleracao, int direcao) {
  int magnitude = abs(aceleracao);
  bool reverse = aceleracao < 0;
  int m1_power = magnitude;
  int m2_power = magnitude;
  bool m1_dir = reverse ? LOW : HIGH;
  bool m2_dir = reverse ? LOW : HIGH;
  
  // Zona neutra (reto)
  if (direcao >= -30 && direcao <= 30) {
    m1_power = magnitude;
    m2_power = magnitude;
    m1_dir = reverse ? LOW : HIGH;
    m2_dir = reverse ? LOW : HIGH;
  }
  // Curva para a esquerda
  else if (direcao < -30) {
    float fator = (float)(-direcao - 30) / (254 - 30);
    m1_power = int(magnitude * fator);
    m2_power = int(magnitude * (0.9 + 0.1 * fator));
    m1_dir = reverse ? HIGH : LOW;
    m2_dir = reverse ? LOW : HIGH;
  }
  // Curva para a direita
  else if (direcao > 30) {
    float fator = (float)(direcao - 30) / (254 - 30);
    m1_power = int(magnitude * (0.9 + 0.1 * fator));
    m2_power = int(magnitude * fator);
    m1_dir = reverse ? LOW : HIGH;
    m2_dir = reverse ? HIGH : LOW;
  }
  
  // *** PROTEÇÃO ANTI-REVERSÃO BRUSCA ***
  // Impede inverter direção em alta velocidade (destrói mecânica!)
  if (m1_dir != motor1_dirCurrent && motor1_current > MIN_SPEED_FOR_DIRECTION_CHANGE) {
    // Forçar parada antes de inverter
    m1_power = 0;
  }
  if (m2_dir != motor2_dirCurrent && motor2_current > MIN_SPEED_FOR_DIRECTION_CHANGE) {
    m2_power = 0;
  }
  
  // Salvar alvos
  motor1_target = constrain(m1_power, 0, 255);
  motor2_target = constrain(m2_power, 0, 255);
  motor1_dirTarget = m1_dir;
  motor2_dirTarget = m2_dir;
}

// ===== APLICAR RAMPA SUAVE =====
void applySmoothing() {
  // *** SOFT-START INTELIGENTE ***
  // Acelera mais rápido em baixas velocidades
  int accel_step = MAX_ACCEL_STEP;
  if (motor1_current < 50) accel_step = MAX_ACCEL_STEP * 2; // 2x mais rápido abaixo de 50
  
  // Motor 1
  if (motor1_current < motor1_target) {
    motor1_current = min(motor1_current + accel_step, motor1_target);
  } else if (motor1_current > motor1_target) {
    motor1_current = max(motor1_current - MAX_DECEL_STEP, motor1_target);
  }
  
  // Só muda direção quando parado ou velocidade baixa
  if (motor1_current <= MIN_SPEED_FOR_DIRECTION_CHANGE) {
    motor1_dirCurrent = motor1_dirTarget;
  }
  
  // Motor 2
  accel_step = MAX_ACCEL_STEP;
  if (motor2_current < 50) accel_step = MAX_ACCEL_STEP * 2;
  
  if (motor2_current < motor2_target) {
    motor2_current = min(motor2_current + accel_step, motor2_target);
  } else if (motor2_current > motor2_target) {
    motor2_current = max(motor2_current - MAX_DECEL_STEP, motor2_target);
  }
  
  if (motor2_current <= MIN_SPEED_FOR_DIRECTION_CHANGE) {
    motor2_dirCurrent = motor2_dirTarget;
  }
}

// ===== ATUALIZAR SAÍDAS DOS MOTORES =====
void updateMotors() {
  if (emergencyStop) {
    stopMotors(true);
    return;
  }
  
  // Motor 1
  digitalWrite(motor1_dir, motor1_dirCurrent);
  digitalWrite(motor1_brake, motor1_current == 0 ? HIGH : LOW);
  analogWrite(motor1_pwm, motor1_current);
  
  // Motor 2
  digitalWrite(motor2_dir, motor2_dirCurrent);
  digitalWrite(motor2_brake, motor2_current == 0 ? HIGH : LOW);
  analogWrite(motor2_pwm, motor2_current);
}

// ===== PARAR MOTORES =====
void stopMotors(bool useBrake) {
  motor1_current = 0;
  motor2_current = 0;
  motor1_target = 0;
  motor2_target = 0;
  
  digitalWrite(motor1_brake, useBrake ? HIGH : LOW);
  digitalWrite(motor2_brake, useBrake ? HIGH : LOW);
  analogWrite(motor1_pwm, 0);
  analogWrite(motor2_pwm, 0);
}

// ===== ENVIAR FEEDBACK =====
void sendFeedback() {
  Serial.print("STATUS:");
  Serial.print(motor1_current);
  Serial.print(",");
  Serial.print(motor2_current);
  Serial.print(",");
  Serial.print(emergencyStop ? "STOP" : (degradedMode ? "DEGRADED" : "OK"));
  Serial.print(",E:");
  Serial.println(errorCount);
}

// ===== ENVIAR STATUS DETALHADO =====
void sendDetailedStatus() {
  unsigned long uptime = (millis() - startTime) / 1000;
  
  Serial.println("===== ARDUINO STATUS =====");
  Serial.print("Uptime: ");
  Serial.print(uptime);
  Serial.println("s");
  
  Serial.print("Motor1: ");
  Serial.print(motor1_current);
  Serial.print(" PWM, DIR=");
  Serial.println(motor1_dirCurrent ? "FWD" : "REV");
  
  Serial.print("Motor2: ");
  Serial.print(motor2_current);
  Serial.print(" PWM, DIR=");
  Serial.println(motor2_dirCurrent ? "FWD" : "REV");
  
  Serial.print("Commands: ");
  Serial.print(commandCount);
  Serial.print(" (Success: ");
  Serial.print(successCount);
  Serial.print(", Errors: ");
  Serial.print(errorCount);
  Serial.println(")");
  
  Serial.print("Error Rate: ");
  if (commandCount > 0) {
    Serial.print((errorCount * 100) / commandCount);
    Serial.println("%");
  } else {
    Serial.println("N/A");
  }
  
  Serial.print("Mode: ");
  Serial.println(emergencyStop ? "EMERGENCY" : (degradedMode ? "DEGRADED" : "NORMAL"));
  
  Serial.println("==========================");
}

// ===== ATUALIZAR LED DE STATUS =====
void updateStatusLED(unsigned long currentTime) {
  if (emergencyStop) {
    // LED fixo em emergência
    digitalWrite(LED_STATUS, HIGH);
  } else if (degradedMode) {
    // LED pisca rápido em modo degradado (200ms)
    if (currentTime - lastLedToggle > 200) {
      ledState = !ledState;
      digitalWrite(LED_STATUS, ledState);
      lastLedToggle = currentTime;
    }
  } else {
    // LED pisca normal (500ms)
    if (currentTime - lastLedToggle > LED_BLINK_INTERVAL) {
      ledState = !ledState;
      digitalWrite(LED_STATUS, ledState);
      lastLedToggle = currentTime;
    }
  }
}

// ===== FILTRO ANTI-TREMOR =====
int applyFilter(int newValue, int* history) {
  // Adiciona novo valor ao histórico
  history[filterIndex] = newValue;
  
  // Calcula média das últimas FILTER_SAMPLES amostras
  int sum = 0;
  for (int i = 0; i < FILTER_SAMPLES; i++) {
    sum += history[i];
  }
  
  filterIndex = (filterIndex + 1) % FILTER_SAMPLES;
  
  return sum / FILTER_SAMPLES;
}

// ===== VERIFICAR MODO DEGRADADO =====
void checkDegradedMode() {
  if (consecutiveErrors >= MAX_ERRORS_BEFORE_DEGRADED) {
    degradedMode = true;
    Serial.println("ARDUINO:WARNING:DEGRADED_MODE");
  }
}

// ===== RESETAR SISTEMA =====
void resetSystem() {
  // Para motores
  stopMotors(true);
  
  // Reset estatísticas
  errorCount = 0;
  consecutiveErrors = 0;
  commandCount = 0;
  successCount = 0;
  
  // Reset estados
  emergencyStop = false;
  degradedMode = false;
  
  // Reset filtros
  for (int i = 0; i < FILTER_SAMPLES; i++) {
    accelHistory[i] = 0;
    dirHistory[i] = 0;
  }
  filterIndex = 0;
  
  // Reset timers
  lastCommandTime = millis();
  startTime = millis();
  
  // LED indica reset (pisca 2x rápido)
  for (int i = 0; i < 2; i++) {
    digitalWrite(LED_STATUS, HIGH);
    delay(100);
    digitalWrite(LED_STATUS, LOW);
    delay(100);
  }
}
