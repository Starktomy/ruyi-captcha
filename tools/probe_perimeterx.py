"""Inspect a live PerimeterX page using ff-ruyijs; a loaded widget is not success."""
import argparse
import json
import math
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from ruyi_captcha.runtime import load_bindings
from ruyi_captcha.providers.turnstile import _linux_profile

STATE = r'''(() => {
 const localName=Object.getOwnPropertyDescriptor(Element.prototype,'localName').get;
 const attribute=Element.prototype.getAttribute;
 const frameDocument=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentDocument').get;
 const shadow=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get;
 const name=e=>localName.call(e),attr=(e,k)=>attribute.call(e,k);
 const scopes=[{node:document,path:'document',depth:0,visible:true}],documents=[],buttons=[];
 for(let index=0;index<scopes.length && scopes.length<80;index++){
   const scope=scopes[index],root=scope.node;
   if(root.nodeType===9){
     const win=root.defaultView;
     documents.push({url:root.URL,title:root.title,ready:root.readyState,
       text:[...root.querySelectorAll('h1,h2,p,label,button')].map(e=>e.textContent.trim()).join('\n').slice(0,1800),
       cookies:(root.cookie||'').split(';').map(s=>s.trim().split('=')[0]).filter(Boolean),
       scripts:[...root.scripts].map(s=>s.src).filter(Boolean).slice(0,20),
       appId:win?._pxAppId,pxCaptcha:!!root.querySelector('#px-captcha')});
   }
   const nodes=[...root.querySelectorAll('*')];
   for(let i=0;i<nodes.length;i++){
     const node=nodes[i],path=scope.path+'.querySelectorAll("*")['+i+']';
     const label=(node.textContent||'').trim();
     if(name(node)==='button' || attr(node,'role')==='button' ||
        name(node)==='input' && ['button','checkbox'].includes(node.type)){
       const rect=node.getBoundingClientRect();
       buttons.push({path,tag:node.localName,id:node.id,role:node.getAttribute('role'),
         label:label.slice(0,300),aria:node.getAttribute('aria-label'),visible:scope.visible,rect:rect.toJSON()});
     }
     const inner=shadow.call(node);
     if(inner && scope.depth<5)scopes.push({node:inner,
       path:'Object.getOwnPropertyDescriptor(Element.prototype,"shadowRoot").get.call('+path+')',
       depth:scope.depth+1,visible:scope.visible});
     if(name(node)==='iframe' && scope.depth<5){
       try{const doc=frameDocument.call(node);if(doc)scopes.push({node:doc,
         path:'Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,"contentDocument").get.call('+path+')',
         depth:scope.depth+1,visible:scope.visible&&!/display\s*:\s*none/.test(attr(node,'style')||'')})}catch(e){}
     }
   }
 }
 return {documents,buttons,scopes:scopes.length,
   roots:scopes.map(s=>({path:s.path,type:s.node.nodeType,
     markup:s.node.nodeType===11?(s.node.innerHTML||'').replace(/<style\b[\s\S]*?<\/style>/gi,'[style]')
       .replace(/<script\b[\s\S]*?<\/script>/gi,'[script]').replace(/\btoken="[^"]*"/gi,'token="[omitted]"')
       .replace(/data:[^"'<>\s]+/g,'data:[omitted]').slice(0,7000):undefined,
     elements:[...s.node.querySelectorAll('*')]
     .filter(e=>!['script','style','link','meta'].includes(name(e))).slice(0,100)
     .map(e=>({tag:name(e),nodeName:e.nodeName,type:Object.prototype.toString.call(e),
               id:attr(e,'id'),role:attr(e,'role'),tabindex:attr(e,'tabindex'),
               aria:attr(e,'aria-label'),class:attr(e,'class'),
               text:['p','label','button','span','div'].includes(name(e))?(e.textContent||'').trim().slice(0,300):undefined}))}))};
})()'''


def public_url(url):
    if url.startswith('data:'):
        return url.split(',',1)[0]+',[data omitted]'
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://nopecha.com/captcha/perimeterx')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--profile', choices=('linux','default'), default='linux')
    parser.add_argument('--shell', type=Path)
    parser.add_argument('--hold', type=float, default=0, help='Send trusted down/up with the event loop running between them')
    parser.add_argument('--output', type=Path, default=Path('artifacts/perimeterx/probe.json'))
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout<=0 or not math.isfinite(args.hold) or not 0<=args.hold<=15:
        parser.error('timeout must be positive and hold must be between 0 and 15 seconds')
    bindings = load_bindings()
    report = {'url':args.url,'profile':args.profile,'verified':False,'errors':[],'states':[],'input':[]}
    started=monotonic()
    with bindings.Context(shell=args.shell,timeout=30,proxy=None,tls='159') as context:
        context.configure_clock('system')
        if args.profile=='linux':context.configure_fingerprint(_linux_profile())
        context.navigate(args.url)
        last=None
        held=None
        hold_started=None
        released=False
        try:
            while monotonic()-started<args.timeout:
                context.timeout=min(15,max(0.5,args.timeout-(monotonic()-started)))
                try:context.advance(100,max_tasks=400)
                except bindings.JSError as error:
                    record={'name':error.name,'message':error.message,'stack':error.stack}
                    if record not in report['errors']:report['errors'].append(record)
                state=json.loads(context.eval('JSON.stringify('+STATE+')'))
                for document in state['documents']:
                    document['url']=public_url(document['url'])
                    document['scripts']=[public_url(url) for url in document['scripts']]
                if state!=last:
                    report['states'].append({'elapsed':round(monotonic()-started,3),'state':state})
                    print(json.dumps(report['states'][-1],ensure_ascii=False),flush=True)
                    last=state
                if args.hold and held is None:
                    candidates=[button for button in state['buttons'] if button.get('visible') and
                        'Press & Hold' in (button.get('aria') or '') and button['rect']['width']>0]
                    if candidates:
                        held=candidates[0]
                        context.eval('globalThis.__ruyiHeldTarget=('+held['path']+');true')
                        held['path']='globalThis.__ruyiHeldTarget'
                        rect=held['rect']
                        init={'clientX':round(rect['x']+rect['width']/2),'clientY':round(rect['y']+rect['height']/2),
                              'button':0,'buttons':0,'bubbles':True,'composed':True,'cancelable':True,'detail':1}
                        screen=context.eval('(()=>{const w=('+held['path']+').ownerDocument.defaultView;return [w.mozInnerScreenX,w.mozInnerScreenY]})()')
                        init.update(screenX=screen[0]+init['clientX'],screenY=screen[1]+init['clientY'])
                        pointer={'pointerId':0,'pointerType':'mouse','isPrimary':True,'width':1,'height':1}
                        for event in ('pointerover','pointerenter','mouseover','mouseenter','pointermove','mousemove','pointerdown','mousedown'):
                            fields={**init,**(pointer if event.startswith('pointer') else {})}
                            if event in ('pointerover','pointerenter','pointermove'):fields['button']=-1
                            if event in ('pointerenter','mouseenter'):fields['bubbles']=False
                            if event not in ('pointerdown','mousedown'):fields['detail']=0
                            if event in ('pointerdown','mousedown'):fields['buttons']=1
                            if event.startswith('pointer'):fields['pressure']=0.5 if fields['buttons'] else 0
                            context.dispatch_input(event,target=held['path'],**fields)
                            if event=='mousedown':context.eval('globalThis.__ruyiHeldTarget.focus();true')
                        hold_started=monotonic()
                        report['input'].append({'event':'down','elapsed':round(monotonic()-started,3)})
                if held is not None and not released and monotonic()-hold_started>=args.hold:
                    for event in ('pointerup','mouseup','click'):
                        fields={**init,**(pointer if event.startswith('pointer') or event=='click' else {})}
                        fields['buttons']=0
                        if event in ('pointerup','click'):fields['pressure']=0
                        context.dispatch_input(event,target=held['path'],**fields)
                    released=True
                    report['input'].append({'event':'up','elapsed':round(monotonic()-started,3)})
        except Exception as error:
            report['error']=f'{type(error).__name__}: {error}'
        if not context.closed:
            context.timeout=5
            report['network']=[{key:(public_url(row[key]) if key=='url' else row[key])
                for key in ('method','url','status','error') if key in row} for row in context.net_log()]
            report['console']=[{key:row[key] for key in ('level','text','realm') if key in row}
                               for row in context.console_log() if row.get('level') in ('error','warn')]
    report['elapsed']=round(monotonic()-started,3)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'elapsed':report['elapsed'],'verified':report['verified'],'errors':len(report['errors']),
                      'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
