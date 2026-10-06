"""Chat with a brand's assistant in the terminal, no API keys or webhooks needed.

    python -m scripts.chat kirana-express
    python -m scripts.chat nimbus-telecom --channel voice
"""
import argparse

from app.config import load_brands
from app.engine import Assistant


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brand", nargs="?", default="kirana-express")
    ap.add_argument("--channel", default="whatsapp", choices=["whatsapp", "rcs", "voice", "web"])
    args = ap.parse_args()
    bot = Assistant(load_brands())
    print(f"Chatting with {bot.brands[args.brand].name} on {args.channel}. Type /insights or /quit.\n")
    while True:
        try:
            text = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/quit":
            break
        if text == "/insights":
            print(bot.store.insights(args.brand))
            continue
        r = bot.handle(args.brand, "cli-user", args.channel, text)
        print(f"bot > {r.text}")
        for b in r.buttons:
            print(f"      [{b.title}]  (type: {b.id if b.id.startswith('faq:') else b.title})")
        print(f"      intent={r.intent} confidence={r.confidence}\n")


if __name__ == "__main__":
    main()
