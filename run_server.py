"""
Inicializador local do tVerbum para Windows.
Verifica porta disponível, abre o navegador automaticamente e inicia o servidor FastAPI.
"""

import os
import sys
import socket
import threading
import time
import webbrowser
import uvicorn
from dotenv import load_dotenv

# Garante que o diretório raiz do projeto esteja no sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def find_available_port(start_port: int = 8000, max_tries: int = 10) -> int:
    """Encontra uma porta livre a partir de start_port."""
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def open_browser_delayed(url: str, delay: float = 1.5):
    """Abre o navegador padrão após o servidor iniciar."""
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    preferred_port = int(os.getenv("PORT", 8000))
    port = find_available_port(preferred_port)
    url = f"http://localhost:{port}"

    print("=" * 60)
    print("  tVerbum v1.1 - Gerador Inteligente de Atas Plenarias")
    print("=" * 60)
    print(f"  Interface Web : {url}")
    print(f"  Swagger API   : {url}/docs")
    print("  Para encerrar : Pressione CTRL+C ou feche esta janela.")
    print("=" * 60)

    if "--no-browser" not in sys.argv:
        threading.Thread(target=open_browser_delayed, args=(url,), daemon=True).start()

    uvicorn.run("tverbum.api:app", host="127.0.0.1", port=port, reload=False)
