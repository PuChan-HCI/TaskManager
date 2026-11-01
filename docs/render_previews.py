#!/usr/bin/python3
"""Render labelled interface illustrations; these are not native screenshots."""
import math
import sys
from pathlib import Path
import cairo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from native import HistoryChart, draw_cpu_history, draw_engine_history, core_color

OUT=Path(__file__).resolve().parent/'previews'
BG='#0d1117'; PANEL='#151e2a'; LINE='#29384c'; TEXT='#e6edf3'; MUTED='#91a2b8'; BLUE='#8cc3ff'

def rgb(value):
    return tuple(int(value[i:i+2],16)/255 for i in (1,3,5))

def rect(x,y,w,h,fill,r=0,stroke=None):
    cr.new_path()
    if r:
        cr.arc(x+w-r,y+r,r,-math.pi/2,0);cr.arc(x+w-r,y+h-r,r,0,math.pi/2)
        cr.arc(x+r,y+h-r,r,math.pi/2,math.pi);cr.arc(x+r,y+r,r,math.pi,math.pi*1.5);cr.close_path()
    else:cr.rectangle(x,y,w,h)
    cr.set_source_rgb(*rgb(fill));cr.fill_preserve()
    if stroke:cr.set_source_rgb(*rgb(stroke));cr.set_line_width(1);cr.stroke()
    else:cr.new_path()

def text(x,y,value,size=13,color=TEXT,bold=False):
    cr.select_font_face('DejaVu Sans',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size);cr.set_source_rgb(*rgb(color));cr.move_to(x,y);cr.show_text(str(value))

def line(x,y,x2,y2,color=LINE):
    cr.set_source_rgb(*rgb(color));cr.set_line_width(1);cr.move_to(x,y);cr.line_to(x2,y2);cr.stroke()

samples=[]
for i in range(60):
    cores=[max(0,min(100,25+19*math.sin(i*.13+n*.6)+12*math.cos(i*.31+n*.9)+n*.9)) for n in range(20)]
    samples.append({'cores':cores,'CPU':sum(cores)/20,'Memory':37+i*.045,'GPU':45+18*math.sin(i*.11),'Disk':10+9*math.sin(i*.15),'rx':1e6*(1.6+math.sin(i*.1)),'tx':1e5*(2+math.cos(i*.15)), 'engines':{'compute':45+18*math.sin(i*.11),'memory':28+5*math.cos(i*.13),'encode':15+10*math.sin(i*.2),'decode':8+4*math.cos(i*.2)}})

def shell(page,resource):
    global cr
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1440,1000);cr=cairo.Context(surface)
    rect(0,0,1440,1000,BG);rect(0,0,1440,58,'#141b24');line(0,58,1440,58)
    text(25,35,'TaskManager',16,bold=True)
    rect(1170,16,116,27,'#14352e',13);text(1185,34,'Live telemetry',10,'#82dbb1')
    rect(1304,15,106,29,'#223246',6);text(1331,35,'Pause',12)
    rect(0,59,214,941,'#111821');line(214,59,214,1000)
    text(24,106,'TaskManager',20,bold=True);text(24,147,'SYSTEM WORKSPACE',10,MUTED,True)
    for i,title in enumerate(['Performance','Processes','System']):
        y=175+i*55
        if title==page:rect(14,y,184,44,'#1b3552',8);rect(14,y+10,3,24,'#73b6ff',1)
        text(33,y+27,title,13,BLUE if title==page else MUTED,title==page)
    text(24,901,'LOCAL MONITORING',10,MUTED,True);text(24,929,'Read-only telemetry',11,MUTED);text(24,953,'v1.0.0',10,MUTED)
    text(248,105,page,28,bold=True)
    text(248,132,'linux-workstation  /  arm64  /  20 logical processors',12,MUTED)
    text(929,103,'INTERFACE PREVIEW',11,'#edc079',True)
    text(929,125,'Demonstration data - not a desktop capture',10,MUTED)
    if page=='Performance':
        for i,(name,value) in enumerate([('CPU','34.2%'),('Memory','39.7%'),('GPU','47.3%'),('Disk','12.4%'),('Network','2.1 MiB/s')]):
            x=248+i*232
            rect(x,159,218,88,'#19324c' if name==resource else PANEL,10,BLUE if name==resource else LINE)
            text(x+17,185,name.upper(),10,MUTED,True);text(x+17,222,value,24,bold=True)
    return surface

def chart(resource,x=248,y=298,w=1160,h=220):
    cr.save();cr.translate(x,y);cr.rectangle(0,0,w,h);cr.clip()
    if resource=='CPU':draw_cpu_history(cr,w,h,samples)
    else:
        class Series:COLORS=HistoryChart.COLORS
        series=Series();series.resource=resource;series.samples=samples
        HistoryChart.draw(series,None,cr,w,h)
    cr.restore()
    text(x,y+h+20,'60 samples',10,MUTED);text(x+w-28,y+h+20,'Now',10,MUTED)

def stats(items,y):
    for i,(title,value) in enumerate(items):
        x=248+i*293
        rect(x,y,278,84,PANEL,9,LINE);text(x+16,y+27,title,11,MUTED);text(x+16,y+61,value,21,BLUE)

def table(headers,rows,y,widths=None):
    x=248;w=1160
    widths=widths or [w/len(headers)]*len(headers)
    rect(x,y,w,40,'#1a2636',7,LINE)
    at=x
    for title,width in zip(headers,widths):text(at+12,y+25,title,10,MUTED,True);at+=width
    for n,row in enumerate(rows):
        ty=y+40+n*43
        rect(x,ty,w,43,'#15202e' if n%2==0 else '#111a26')
        at=x
        for value,width in zip(row,widths):
            cr.save();cr.rectangle(at+8,ty,width-14,43);cr.clip();text(at+12,ty+27,value,11);cr.restore();at+=width
        line(x,ty+43,x+w,ty+43)


def render(name):
    page='Processes' if name=='processes' else 'System' if name=='system' else 'Performance'
    resource={'cpu':'CPU','memory':'Memory','gpu':'GPU','gpu-processes':'GPU','disk':'Disk','network':'Network'}.get(name)
    surface=shell(page,resource)
    if name=='cpu':
        text(248,281,'CPU  /  All logical processors',21,BLUE);chart('CPU')
        stats([('Processes','284'),('Threads','1,762'),('Average clock','2.84 GHz'),('Uptime','7.5 hours')],565)
        text(248,679,'LOGICAL PROCESSORS - SCROLL TO EXPLORE ALL 20 CPUS',10,MUTED,True)
        cr.save();cr.rectangle(248,698,1147,258);cr.clip()
        for i in range(20):
            x=248+(i%5)*230;y=699+(i//5)*118
            rect(x,y,217,106,PANEL,8,LINE)
            color='#'+''.join(f'{round(c*255):02x}' for c in core_color(i))
            text(x+12,y+25,f'CPU {i+1}    {samples[-1]["cores"][i]:.1f}%',11,color,True)
            cr.save();cr.translate(x+10,y+37);cr.rectangle(0,0,197,58);cr.clip();draw_cpu_history(cr,197,58,samples,i);cr.restore()
        cr.restore();rect(1401,699,6,258,'#25384c',3);rect(1401,700,6,124,'#5c7693',3)
    elif name=='memory':
        text(248,281,'Memory  /  System usage',21,BLUE);chart('Memory')
        stats([('In use','49.7 GiB'),('Available','75.8 GiB'),('Cached','38.2 GiB'),('Swap used','0.2 GiB')],565)
        text(248,704,'System memory in use = total - available.',12,MUTED)
        text(248,735,'Integrated GPUs may share system memory.',12,MUTED)
    elif name=='gpu':
        text(248,281,'GPU  /  Supported engine activity',21,BLUE)
        for i,(key,title) in enumerate([('compute','Compute / graphics'),('memory','Memory controller'),('encode','Video encode'),('decode','Video decode')]):
            x=248+(i%2)*590;y=305+(i//2)*237
            rect(x,y,570,218,PANEL,10,LINE);text(x+17,y+31,title+'  /  '+str(round(samples[-1]['engines'][key],1))+'%',14,BLUE)
            cr.save();cr.translate(x+15,y+51);cr.rectangle(0,0,540,148);cr.clip();draw_engine_history(cr,540,148,samples,key);cr.restore()
        stats([('Temperature','51 C'),('Power','48 W'),('Clock','1,980 MHz'),('GPU memory','3.2 GiB')],811)
        text(248,938,'Only confirmed metrics are displayed. Engine availability depends on the device and driver.',11,MUTED)
    elif name=='gpu-processes':
        text(248,287,'GPU PROCESSES',12,MUTED,True);text(1244,287,'All GPUs',12,BLUE)
        table(['PID','User','GPU','Type','GPU %','GPU memory','Encode','Decode','Process'],[
            ['6284','user','0 NVIDIA','Compute','48.3%','2.4 GiB','0.0%','0.0%','python3'],
            ['2031','user','0 NVIDIA','Graphics','4.1%','620 MiB','0.0%','0.0%','gnome-shell'],
            ['9120','user','0 NVIDIA','Graphics','2.8%','280 MiB','12.1%','0.0%','video-editor'],
            ['1024','user','1 Intel','DRM','1.7%','-','-','0.0%','browser'],
        ],312,[72,82,118,112,92,145,100,100,339])
        text(248,580,'GPU %: NVIDIA SM activity or busiest DRM engine. Missing values are not reported as zero.',12,MUTED)
        text(248,610,'Every column supports sorting. CPU usage and host memory are also available in the native table.',12,MUTED)
    elif name=='disk':
        text(248,281,'Disk  /  Read and write activity',21,BLUE);chart('Disk',h=164)
        stats([('Read','24.8 MiB/s'),('Write','8.2 MiB/s'),('Root used','1.3 TiB'),('Root free','2.1 TiB')],492)
        text(248,613,'FILE SYSTEMS',11,MUTED,True)
        table(['Device','Mount point','Type','Total','Used','Available','Usage'],[
            ['/dev/nvme0n1p2','/','ext4','3.6 TiB','1.3 TiB','2.1 TiB','38.8%'],
            ['/dev/nvme0n1p1','/boot/efi','vfat','297 MiB','12.7 MiB','284 MiB','4.3%'],
            ['/dev/sda1','/media/data','ext4','1.8 TiB','642 GiB','1.1 TiB','34.7%'],
        ],635,[205,210,125,155,155,180,130])
    elif name=='network':
        text(248,281,'Network  /  Receive and send',21,BLUE);chart('Network',h=164)
        stats([('Receive','1.8 MiB/s'),('Send','320 KiB/s'),('Interfaces','4'),('Active links','3')],492)
        text(248,613,'NETWORK INTERFACES',11,MUTED,True)
        table(['Interface','Status','Receive /s','Send /s','Total received','Total sent'],[
            ['enp1s0','Up','1.7 MiB','290 KiB','12.8 GiB','1.4 GiB'],['wlan0','Up','92 KiB','30 KiB','1.9 GiB','810 MiB'],
            ['docker0','Up','12 KiB','4 KiB','2.2 GiB','1.2 GiB'],['tailscale0','Unknown','0 B','0 B','98 MiB','42 MiB']
        ],635,[220,120,200,200,210,210])
        text(248,930,'Totals since boot or interface creation. Includes virtual interfaces.',11,MUTED)
    elif name=='processes':
        rect(248,166,1160,44,PANEL,8,LINE);text(267,193,'Search process name or PID...',13,MUTED)
        text(248,244,'284 processes  /  CPU: 100% = one logical core  /  Memory: resident set size',12,MUTED)
        table(['Name','PID','CPU (descending)','Memory','Threads','State'],[
            ['python3','6284','72.4%','3.8 GiB','24','Running'],['browser','5401','12.7%','1.2 GiB','42','Running'],['gnome-shell','2031','4.1%','620 MiB','18','Sleeping'],['taskmanager','9182','1.6%','84 MiB','8','Running'],['pipewire','1890','0.8%','42 MiB','5','Sleeping'],['systemd','1','0.0%','18 MiB','1','Sleeping'],['NetworkManager','892','0.0%','22 MiB','4','Sleeping']
        ],269,[350,105,195,190,135,185])
    elif name=='system':
        entries=[('Host','linux-workstation'),('Architecture','arm64'),('Logical CPUs','20'),('Total memory','125.5 GiB'),('Kernel','Linux 6.x'),('Root capacity','3.6 TiB'),('GPU 0','NVIDIA GPU'),('GPU driver','Available driver telemetry'),('CPU temperature','48.0 C'),('GPU temperature','51.0 C'),('Load averages','1.24 / 1.06 / 0.98'),('Collection mode','Local / read-only')]
        for i,(title,value) in enumerate(entries):
            x=248+(i%3)*393;y=172+(i//3)*144
            rect(x,y,373,122,PANEL,10,LINE);text(x+18,y+35,title,12,MUTED);text(x+18,y+78,value,17,BLUE,True)
    OUT.mkdir(parents=True,exist_ok=True)
    surface.write_to_png(str(OUT/(name+'.png')))
    print(OUT/(name+'.png'))

if __name__=='__main__':
    for name in ['cpu','memory','gpu','gpu-processes','disk','network','processes','system']:render(name)
