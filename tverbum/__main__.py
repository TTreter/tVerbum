"""
Ponto de entrada CLI para o tVerbum (python -m tverbum).
"""

import argparse
import uvicorn
from .models import MinuteConfig
from .ai_engine import MinuteGenerator
from .exporter import MinuteExporter


def main():
    parser = argparse.ArgumentParser(description="tVerbum - Gerador de Atas com IA")
    subparsers = parser.add_subparsers(dest="command")

    # Comando: serve
    serve_parser = subparsers.add_parser("serve", help="Inicia o servidor web e a API REST")
    serve_parser.add_argument("--port", type=int, default=8000, help="Porta HTTP (padrão: 8000)")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host HTTP (padrão: 0.0.0.0)")

    # Comando: generate
    gen_parser = subparsers.add_parser("generate", help="Gera uma ata via linha de comando")
    gen_parser.add_argument("--url", required=True, help="URL do vídeo no YouTube")
    gen_parser.add_argument("--number", required=True, help="Número da ata (ex: 21/2026)")
    gen_parser.add_argument("--preset", default=None, help="ID do perfil institucional (ex: camara_exemplo)")
    gen_parser.add_argument("--org", default="", help="Nome do órgão/entidade")
    gen_parser.add_argument("--type", default="Sessão Ordinária", help="Tipo de sessão")
    gen_parser.add_argument(
        "--mode",
        choices=["auto", "youtube_direct", "captions", "audio"],
        default="auto",
        help="Modo de leitura do vídeo",
    )
    gen_parser.add_argument("--paragraphs", type=int, default=1, help="Quantidade de parágrafos (1 = parágrafo único)")
    gen_parser.add_argument(
        "--detail",
        choices=["resumido", "padrao", "detalhado"],
        default="padrao",
        help="Nível de detalhamento",
    )
    gen_parser.add_argument("--out", default="ata_gerada.pdf", help="Arquivo de saída (.pdf, .docx, .md, .txt)")

    args = parser.parse_args()

    if args.command == "serve" or args.command is None:
        port = getattr(args, "port", 8000)
        host = getattr(args, "host", "0.0.0.0")
        print(f"🏛️ Iniciando tVerbum em http://localhost:{port}")
        uvicorn.run("tverbum.api:app", host=host, port=port, reload=True)
    elif args.command == "generate":
        print(f"Processando vídeo: {args.url} (modo: {args.mode})...")
        config = MinuteConfig(
            video_url=args.url,
            session_number=args.number,
            preset_id=args.preset,
            organization_name=args.org,
            session_type=args.type,
            ingestion_mode=args.mode,
            single_paragraph=(args.paragraphs == 1),
            paragraph_count=args.paragraphs,
            detail_level=args.detail,
        )
        generator = MinuteGenerator()
        output = generator.generate(config)

        out_path = args.out
        if out_path.lower().endswith(".pdf"):
            MinuteExporter.to_pdf(output, out_path)
        elif out_path.lower().endswith(".docx"):
            MinuteExporter.to_docx(output, out_path)
        elif out_path.lower().endswith(".md"):
            MinuteExporter.to_markdown(output, out_path)
        else:
            MinuteExporter.to_txt(output, out_path)
        print(f"✅ Ata gerada com sucesso salva em: {out_path}")


if __name__ == "__main__":
    main()
