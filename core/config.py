from datetime import datetime
import json
import logging
import os


class Config:

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.ano = 2026
        self.salvar_log = True
        self.log_detalhado = True
        self.delay_entre_requisicoes = 0.2
        self.output_dir = "outputs"
        self.logs_dir = "logs"
        self._load_config()

    def _load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.ano = data.get("ano", self.ano)
                self.salvar_log = data.get("salvar_log", self.salvar_log)
                self.log_detalhado = data.get(
                    "log_detalhado", self.log_detalhado
                )
                self.delay_entre_requisicoes = data.get(
                    "delay_entre_requisicoes", self.delay_entre_requisicoes
                )
                self.output_dir = data.get("output_dir", self.output_dir)
                self.logs_dir = data.get("logs_dir", self.logs_dir)


def setup_logger(config: Config) -> logging.Logger:
    """Configura o logger principal da aplicação."""
    logger = logging.getLogger("TransparenciaLogger")
    logger.setLevel(logging.DEBUG if config.log_detalhado else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Output no Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if config.log_detalhado else logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # 2. Output em Arquivo .log (Se ativado na config)
    if config.salvar_log:
        os.makedirs(config.logs_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(config.logs_dir, f"execucao_{timestamp}.log")

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG if config.log_detalhado else logging.INFO)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        logger.info(f"📝 Registro de logs ativo. Salvar em: {log_file}")

    return logger