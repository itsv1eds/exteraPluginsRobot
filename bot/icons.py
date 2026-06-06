from __future__ import annotations


ICONS: dict[str, str] = {
    "bot": "5222374383019920631",
    "plugin": "5267452542987574967",
    "all_plugins": "5267452542987574967",
    "catalog": "5267277346976602103",
    "plugin_alt": "5267277346976602103",
    "submit": "5883973610606956186",
    "tools": "5267079576617526256",
    "stats": "5267457005458596717",
    "star": "5958376256788502078",
    "profile": "5267433821225131491",
    "art": "5267135913703547191",
    "joinly": "5269559701187630408",
    "lock": "5879895758202735862",
    "bell": "5266996151172768910",
    "library": "5269342457446831830",
    "game": "5267499538519727119",
    "art_alt": "5269235186343648971",
    "settings": "5877260593903177342",
    "menu": "5875271289605722323",
    "requests": "5883973610606956186",
    "vote": "5886666250158870040",
    "updates": "5877410604225924969",
    "search": "5874960879434338403",
    "open": "5877468380125990242",
    "back": "5875082500023258804",
    "forward": "5877468380125990242",
    "add": "5877219383691972108",
    "delete": "5879896690210639947",
    "edit": "5879841310902324730",
    "file": "5839323457015256759",
    "link": "5877465816030515018",
    "tag": "5843862283964390528",
    "send": "5877540355187937244",
    "download": "5886451926995833684",
    "calendar": "5967412305338568701",
    "clock": "5985616167740379273",
    "yes": "5825794181183836432",
    "no": "5778527486270770928",
    "cancel": "5872829476143894491",
    "warning": "5881702736843511327",
    "broadcast": "5771695636411847302",
    "ban": "5872829476143894491",
    "admin": "5886412370347036129",
    "support": "5891243564309942507",
    "home": "5967822972931542886",
}


CATEGORY_ICONS: dict[str, str] = {
    "informational": ICONS["stats"],
    "utilities": ICONS["tools"],
    "customization": ICONS["art"],
    "fun": ICONS["game"],
    "library": ICONS["library"],
}

CATEGORY_FALLBACKS: dict[str, str] = {
    "informational": "📊",
    "utilities": "🛠",
    "customization": "🎨",
    "fun": "🎮",
    "library": "📚",
}


def emoji_html(key: str, fallback: str) -> str:
    emoji_id = ICONS.get(key) or CATEGORY_ICONS.get(key)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
