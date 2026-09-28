# Tantou avatar mask

`tantou-mask.png` contains a white fill with the alpha silhouette of the official
170×153 rounded hexagon avatar, extracted from:

https://idolmaster-official.jp/assets/img/idol/hexagon/cinderellagirls/abe_nana.png

The saved official My Desk frontend uses these pre-masked PNGs directly. This file
contains no character artwork. The Pillow renderer and browser crop preview share
this same mask and aspect ratio so custom images, QQ avatars and placeholders have
the same outline and vertical alignment as the official icons.
