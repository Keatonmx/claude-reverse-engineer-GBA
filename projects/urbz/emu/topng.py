import sys, struct, zlib
def png(path, w, h, rows):
    raw=b"".join(b"\x00"+r for r in rows)
    def ch(t,d): return struct.pack(">I",len(d))+t+d+struct.pack(">I",zlib.crc32(t+d)&0xFFFFFFFF)
    open(path,"wb").write(b"\x89PNG\r\n\x1a\n"+ch(b"IHDR",struct.pack(">IIBBBBB",w,h,8,2,0,0,0))+ch(b"IDAT",zlib.compress(raw))+ch(b"IEND",b""))
for f in sys.argv[1:]:
    d=open(f,"rb").read(); w,h=240,160
    rows=[]
    for y in range(h):
        row=bytearray()
        for x in range(w):
            px=struct.unpack_from("<I",d,(y*w+x)*4)[0]
            row+=bytes(((px)&0xFF,(px>>8)&0xFF,(px>>16)&0xFF))
        rows.append(bytes(row))
    png(f.replace(".rgba",".png"),w,h,rows)
