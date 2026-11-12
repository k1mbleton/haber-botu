#!/usr/bin/env python3
"""
Botunuza Telegram'dan /start yazdıktan sonra çalıştırın:

    python chat_id_bul.py 123456:ABC-tokeniniz

Bota yazan kişilerin / botun eklendiği grup ve kanalların ID'lerini listeler.
"""
import json
import sys
import urllib.request

if len(sys.argv) != 2:
    sys.exit("Kullanım: python chat_id_bul.py <BOT_TOKEN>")

url = f"https://api.telegram.org/bot{sys.argv[1]}/getUpdates"
with urllib.request.urlopen(url, timeout=30) as r:
    veri = json.loads(r.read())

bulunan = {}
for u in veri.get("result", []):
    for anahtar in ("message", "channel_post", "my_chat_member", "edited_message"):
        chat = (u.get(anahtar) or {}).get("chat")
        if chat:
            ad = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            bulunan[chat["id"]] = f"{chat['type']:<10} {ad}"

if not bulunan:
    print("Hiç mesaj bulunamadı. Telegram'da botunuza /start yazıp tekrar deneyin.")
for cid, bilgi in bulunan.items():
    print(f"{cid:>16}   {bilgi}")
