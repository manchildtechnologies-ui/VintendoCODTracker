"""Create original geometric fallback artwork, with no names or scores baked in."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter
import random
folder=Path(__file__).resolve().parent/'artwork'/'library';folder.mkdir(parents=True,exist_ok=True)
for index,accent in enumerate(((247,130,46),(59,177,178),(148,113,213))):
    rng=random.Random(index);image=Image.new('RGB',(1600,900),(13,18,27));d=ImageDraw.Draw(image,'RGBA')
    for y in range(900):
        tint=max(0,1-abs(y-430)/600);d.line((0,y,1600,y),fill=(int(13+accent[0]*tint*.13),int(18+accent[1]*tint*.13),int(27+accent[2]*tint*.13),255))
    d.ellipse((1050,80,1390,420),fill=(*accent,32),outline=(*accent,110),width=2)
    for i in range(16):
        x=rng.randint(-300,1800);d.line((x,0,x-300,900),fill=(*accent,20),width=rng.randint(1,3))
    d.polygon([(0,620),(200,405),(360,520),(580,320),(800,550),(1050,330),(1270,460),(1450,300),(1600,400),(1600,900),(0,900)],fill=(9,14,22,200))
    for x,y in ((480,465),(690,435),(910,450),(1120,480)):
        d.ellipse((x-30,y-84,x+30,y-24),fill=(10,14,21,255),outline=(*accent,160),width=2)
        d.rounded_rectangle((x-43,y-26,x+43,y+112),radius=18,fill=(9,13,19,255),outline=(*accent,100),width=2)
        d.line((x-20,y+100,x-29,y+230),fill=(8,12,17,255),width=26);d.line((x+20,y+100,x+29,y+230),fill=(8,12,17,255),width=26)
    for i in range(6):d.line((60,65+i*10,210-i*18,65+i*10),fill=(*accent,170),width=2)
    image.save(folder/f'tactical-fallback-{index+1}.jpg',quality=93)
