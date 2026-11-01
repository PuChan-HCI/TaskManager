#!/usr/bin/python3
"""Render explicitly labelled GNOME launch-guide illustrations."""
from pathlib import Path
import cairo
import render_previews as drawing

OUT = Path(__file__).resolve().parent/'previews'


def icon(x, y, scale=1):
    cr = drawing.cr
    cr.save(); cr.translate(x,y); cr.scale(scale,scale)
    drawing.rect(0,0,92,92,'#172331',20)
    drawing.rect(15,18,62,51,'#223c55',6,'#83c5ff')
    cr.set_source_rgb(*drawing.rgb('#83c5ff'));cr.set_line_width(3)
    cr.move_to(20,50);cr.line_to(30,50);cr.line_to(38,32);cr.line_to(49,60);cr.line_to(57,42);cr.line_to(65,50);cr.line_to(72,50);cr.stroke()
    cr.move_to(45,70);cr.line_to(45,78);cr.move_to(34,78);cr.line_to(57,78);cr.stroke()
    cr.restore()


def desktop():
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1280,800)
    drawing.cr=cairo.Context(surface)
    drawing.rect(0,0,1280,800,'#1b1831')
    drawing.rect(0,0,1280,40,'#17151e')
    drawing.text(24,26,'Activities',13,bold=True)
    drawing.text(588,26,'10:42',12)
    drawing.text(1147,26,'Wi-Fi   Sound',11)
    drawing.rect(85,62,1110,51,'#262332',10)
    drawing.text(109,93,'GNOME-style illustration - not a desktop screenshot',14,'#edc079',True)
    drawing.rect(22,154,75,500,'#252130',18)
    for y,color in [(187,'#d29b66'),(263,'#6f9fe2'),(339,'#bc85b9'),(415,'#79ae9d')]:
        drawing.rect(40,y,38,38,color,10)
    for row in range(3):
        for column in range(3):
            drawing.rect(43+column*12,581+row*12,5,5,'#e6edf3',2)
    return surface


def callout(x,y,number,title,body):
    drawing.rect(x,y,500,124,'#111a26',12,'#6faeff')
    drawing.rect(x+16,y+18,31,31,'#1b3552',16)
    drawing.text(x+26,y+40,number,15,'#99caff',True)
    drawing.text(x+61,y+40,title,18,bold=True)
    for i,line in enumerate(body):drawing.text(x+61,y+71+i*23,line,14,'#a6b4c9')


def render_menu():
    surface=desktop()
    drawing.text(164,182,'Open your applications',30,bold=True)
    drawing.text(164,217,'Press Super, or use the desktop launcher.',17,'#b9aecb')
    # Show an example app grid without claiming to reproduce a user's desktop.
    for i,(name,color) in enumerate([('Files','#d29b66'),('Settings','#8c98a8'),('Browser','#6f9fe2'),('Text Editor','#79ae9d')]):
        x=181+(i%2)*191;y=288+(i//2)*146
        drawing.rect(x,y,70,70,color,15);drawing.text(x-2,y+100,name,13)
    callout(648,260,'1','Open the overview',['Press the Super (Windows) key,','or select Activities.'])
    callout(648,414,'or','Browse installed applications',['Click Show Applications: the grid','of dots in the desktop dock.'])
    drawing.rect(32,569,56,57,'#332b49',10,'#a5cbff')
    for r in range(3):
        for c in range(3):drawing.rect(43+c*12,581+r*12,5,5,'#e6edf3',2)
    drawing.line(97,597,622,480,'#91beff')
    drawing.text(164,738,'Your dock position and launcher labels may differ by desktop and version.',14,'#b9aecb')
    surface.write_to_png(str(OUT/'launch-menu.png'))


def render_search():
    surface=desktop()
    drawing.rect(266,156,748,60,'#302c40',14,'#82b9ff')
    drawing.text(292,194,'TaskManager',23)
    drawing.rect(312,264,200,190,'#312d44',15,'#82b9ff')
    icon(366,284)
    drawing.text(348,410,'TaskManager',18,bold=True)
    drawing.text(331,437,'Installed application',12,'#b9aecb')
    callout(648,271,'2','Search for TaskManager',['Type TaskManager in the overview','or application search field.'])
    callout(648,435,'3','Launch the application',['Click the TaskManager icon.','Its main window will open.'])
    drawing.line(517,378,629,490,'#91beff')
    drawing.rect(266,650,748,74,'#171e2b',10)
    drawing.text(287,680,'No terminal needed after installation',18,'#9ed9bb',True)
    drawing.text(287,706,'The installer registers TaskManager with your desktop applications menu.',13,'#b9c7d9')
    surface.write_to_png(str(OUT/'launch-search.png'))


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    render_menu();render_search()
    print('Saved labelled launch guide illustrations in '+str(OUT))
