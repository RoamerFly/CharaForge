"""Deterministic mask drawing, distinct from a model's actual inpainting result."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

class MaskDocument:
    def __init__(self,image):
        self.image=Image.open(image).convert('RGB')
        self.path=str(Path(image).resolve())
        self.alpha=Image.new('L',self.image.size,255)
        self.undo_stack=[]

    def begin(self):
        self.undo_stack.append(self.alpha.copy())
        if len(self.undo_stack)>20:self.undo_stack.pop(0)

    def stroke(self,a,b,radius,erase=False):
        draw=ImageDraw.Draw(self.alpha);value=255 if erase else 0;r=max(1,int(radius))
        draw.line((a,b),fill=value,width=2*r)
        for x,y in (a,b):draw.ellipse((x-r,y-r,x+r,y+r),fill=value)

    def clear(self):self.begin();self.alpha=Image.new('L',self.image.size,255)
    def undo(self):
        if self.undo_stack:self.alpha=self.undo_stack.pop()

    def import_mask(self,path):
        with Image.open(path) as im:
            if im.size!=self.image.size:raise ValueError('蒙版尺寸与待修图不一致。')
            self.begin()
            self.alpha=im.getchannel('A') if 'A' in im.getbands() else Image.eval(im.convert('L'),lambda p:255-p)

    def overlay(self):
        tint=Image.new('RGBA',self.image.size,(244,77,96,0))
        tint.putalpha(Image.eval(self.alpha,lambda p:int((255-p)*.42)))
        return Image.alpha_composite(self.image.convert('RGBA'),tint).convert('RGB')

    def save(self,path,feather=0):
        if self.alpha.getextrema()[0]==255:raise ValueError('请先涂抹需要修复的区域。')
        alpha=self.alpha.filter(ImageFilter.GaussianBlur(feather)) if feather else self.alpha
        im=Image.new('RGBA',self.image.size,(255,255,255,255));im.putalpha(alpha)
        Path(path).parent.mkdir(parents=True,exist_ok=True);im.save(path,'PNG')
        return str(Path(path).resolve())

def keep_outside(original,result,mask,target):
    """Optional postprocess. Retain raw output and record the exact composite operation."""
    with Image.open(original) as a,Image.open(result) as b,Image.open(mask) as m:
        if a.size!=b.size or a.size!=m.size:raise ValueError('保留蒙版外原像素需要原图、结果与蒙版尺寸完全一致，不会自动拉伸。')
        if 'A' not in m.getbands():raise ValueError('蒙版缺少透明通道。')
        result=Image.composite(a.convert('RGB'),b.convert('RGB'),m.getchannel('A'))
        with Path(target).open('xb') as f:result.save(f,'PNG')
    return str(Path(target).resolve())
