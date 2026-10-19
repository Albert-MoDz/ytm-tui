"""Conjuntos de iconos. 'unicode' (por defecto) o 'ascii' para terminales sin buenas fuentes."""

UNICODE = dict(
    prev="|◀", next="▶|", play="▶", pause="‖", back="«", fwd="»", shuffle="⇄", repeat="↻", repeat_all="↻", repeat_one="↻¹",
    vol_dn="♪−", vol_up="♪+", mute="✕", fav="♡", fav_on="♥", dl="⬇", dl_all="⬇⬇", playlist="♫", radio="◉",
    queue="≡", lyrics="✎", cover="▣", viz="▂▅▇", eq="≋", remote="⇆", settings="⚙", help="?", quit="■",
    add="✚", addnext="→", remove="✕", up="▲", down="▼", search="↵", recent="↺", filter="▽", more="⋯",
    songs="♪", albums="☰", artists="☺", music="♬", only="♩", sleep="☾", playall="▶▶", mixall="⇄▶",
    track="♪", collection="☰", channel="☺", sub="✦", export="⇪", import_="⇩",
)

ASCII = dict(
    prev="|<", next=">|", play=">", pause="||", back="<<", fwd=">>", shuffle="Shuf", repeat="Rep", repeat_all="Rep*", repeat_one="Rep1",
    vol_dn="V-", vol_up="V+", mute="Mute", fav="<3", fav_on="<3*", dl="DL", dl_all="DL+", playlist="PL", radio="Radio",
    queue="Cola", lyrics="Letra", cover="Img", viz="Viz", eq="EQ", remote="Web", settings="Cfg", help="?", quit="Q",
    add="+", addnext="+>", remove="x", up="^", down="v", search="Go", recent="Rec", filter="Filt", more="...",
    songs="Song", albums="List", artists="Art", music="YTM", only="<10m", sleep="Zz", playall=">>>", mixall="Shuf>",
    track="-", collection="[]", channel="@", sub="Sub", export="Exp", import_="Imp",
)

_current = UNICODE


def set_icon_set(name: str) -> None:
    global _current
    _current = ASCII if name == "ascii" else UNICODE


def ic(key: str) -> str:
    return _current[key]
