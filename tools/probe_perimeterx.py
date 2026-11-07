"""Inspect a live PerimeterX page using ff-ruyijs; a loaded widget is not success."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from ruyi_captcha.runtime import load_bindings
from ruyi_captcha.providers.turnstile import _linux_profile

INSPECT = r'''(capture => {
 const localName=Object.getOwnPropertyDescriptor(Element.prototype,'localName').get;
 const attribute=Element.prototype.getAttribute;
 const frameDocument=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentDocument').get;
 const shadow=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get;
 const nodeType=Object.getOwnPropertyDescriptor(Node.prototype,'nodeType').get;
 const nodeName=Object.getOwnPropertyDescriptor(Node.prototype,'nodeName').get;
 const textContent=Object.getOwnPropertyDescriptor(Node.prototype,'textContent').get;
 const bounds=Element.prototype.getBoundingClientRect;
 const query=(root,selector)=>Reflect.apply(nodeType.call(root)===9?Document.prototype.querySelectorAll:
   nodeType.call(root)===11?DocumentFragment.prototype.querySelectorAll:Element.prototype.querySelectorAll,root,[selector]);
 const queryPath=(path,type)=>'(Reflect.apply('+(type===9?'Document':type===11?'DocumentFragment':'Element')+
   '.prototype.querySelectorAll,'+path+',["*"]))';
 const text=e=>textContent.call(e)||'';
 const name=e=>localName.call(e),attr=(e,k)=>attribute.call(e,k);
 const scopes=[{node:document,path:'document',depth:0,visible:true}],documents=[],buttons=[];
 if(capture)globalThis.__ruyiPxCandidates={};
 for(let index=0;index<scopes.length && scopes.length<80;index++){
   const scope=scopes[index],root=scope.node;
   const type=nodeType.call(root);
   if(type===9){
     const win=root.defaultView;
     documents.push({url:root.URL,title:root.title,ready:root.readyState,
       text:[...query(root,'h1,h2,p,label,button')].map(e=>text(e).trim()).join('\n').slice(0,1800),
       cookies:(root.cookie||'').split(';').map(s=>s.trim().split('=')[0]).filter(Boolean),
       scripts:[...root.scripts].map(s=>s.src).filter(Boolean).slice(0,20),
       appId:win?._pxAppId,pxCaptcha:!!query(root,'#px-captcha').length});
   }
   const nodes=[...query(root,'*')];
   for(let i=0;i<nodes.length;i++){
     const node=nodes[i],path=queryPath(scope.path,type)+'['+i+']';
     const label=text(node).trim();
     if(name(node)==='button' || attr(node,'role')==='button' ||
        name(node)==='input' && ['button','checkbox'].includes(attr(node,'type'))){
       const rect=bounds.call(node);
       if(capture && scope.visible){
         const aria=attr(node,'aria-label')||'';
         if(aria.includes('Press & Hold'))globalThis.__ruyiPxCandidates.hold=node;
         if(aria==='Accessible challenge')globalThis.__ruyiPxCandidates.accessible=node;
         if(aria==='Press again')globalThis.__ruyiPxCandidates.confirm=node;
       }
       buttons.push({path,tag:name(node),id:attr(node,'id'),role:attr(node,'role'),
         label:label.slice(0,300),aria:attr(node,'aria-label'),visible:scope.visible,rect:rect.toJSON()});
     }
     const inner=shadow.call(node);
     if(inner && scope.depth<5)scopes.push({node:inner,
       path:'Object.getOwnPropertyDescriptor(Element.prototype,"shadowRoot").get.call('+path+')',
       depth:scope.depth+1,visible:scope.visible});
     if(name(node)==='iframe' && scope.depth<5 && !/display\s*:\s*none/.test(attr(node,'style')||'')){
       try{const doc=frameDocument.call(node);if(doc)scopes.push({node:doc,
         path:'Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,"contentDocument").get.call('+path+')',
         depth:scope.depth+1,visible:scope.visible&&!/display\s*:\s*none/.test(attr(node,'style')||'')})}catch(e){}
     }
   }
 }
 return {documents,buttons,scopes:scopes.length,
   roots:scopes.map(s=>({path:s.path,type:nodeType.call(s.node),
     markup:nodeType.call(s.node)===11?(Object.getOwnPropertyDescriptor(ShadowRoot.prototype,'innerHTML').get.call(s.node)||'').replace(/<style\b[\s\S]*?<\/style>/gi,'[style]')
       .replace(/<script\b[\s\S]*?<\/script>/gi,'[script]').replace(/\btoken="[^"]*"/gi,'token="[omitted]"')
       .replace(/data:[^"'<>\s]+/g,'data:[omitted]').slice(0,7000):undefined,
     elements:[...query(s.node,'*')]
     .filter(e=>!['script','style','link','meta'].includes(name(e))).slice(0,100)
     .map(e=>({tag:name(e),nodeName:nodeName.call(e),
               id:attr(e,'id'),role:attr(e,'role'),tabindex:attr(e,'tabindex'),
               aria:attr(e,'aria-label'),class:attr(e,'class'),
               text:['p','label','button','span','div'].includes(name(e))?text(e).trim().slice(0,300):undefined}))}))};
})'''
STATE = INSPECT + '(false)'
CAPTURE_STATE = INSPECT + '(true)'
TARGET_STATE = r'''(() => {
 const target=globalThis.__ruyiHeldTarget;
 return {aria:Element.prototype.getAttribute.call(target,'aria-label'),
   connected:Object.getOwnPropertyDescriptor(Node.prototype,'isConnected').get.call(target)};
})()'''
OBSERVE_SERVER_RESULT = r'''(win => {
 const app=win._pxAppId;
 if(typeof app!=='string')return false;
 const handler=win['_'+app.replace(/px|PX/,'')+'handler'];
 if(!handler || typeof handler.PX764!=='function')return false;
 const observed=globalThis.__ruyiPxObservedHandlers ||= new WeakSet();
 const results=globalThis.__ruyiPxServerResults ||= [];
 if(observed.has(handler))return true;
 const original=handler.PX764;
 handler.PX764=function(status,...args){
   // Store only a bounded numeric status, never the cookie/token arguments.
   const value=typeof status==='number'?status:
     typeof status==='string' && /^-?\d{1,4}$/.test(status)?Number(status):NaN;
   if(Number.isInteger(value) && Math.abs(value)<=9999 && results.length<64)
     results.push({status:value});
   return Reflect.apply(original,this,[status,...args]);
 };
 observed.add(handler);
 return true;
})'''


def public_url(url):
    if url.startswith('data:'):
        return url.split(',',1)[0]+',[data omitted]'
    parsed = urlsplit(url)
    hostname=parsed.hostname or ''
    if ':' in hostname:hostname='['+hostname+']'
    authority=hostname+(':'+str(parsed.port) if parsed.port is not None else '')
    return urlunsplit((parsed.scheme, authority, parsed.path, '', ''))


def input_point(rect, origin):
    """Align a screen point before deriving the frame-local pointer position."""
    screen_x=math.floor(origin[0]+rect['x']+rect['width']/2+0.5)
    screen_y=math.floor(origin[1]+rect['y']+rect['height']/2+0.5)
    return {'clientX':screen_x-origin[0], 'clientY':screen_y-origin[1],
            'screenX':screen_x, 'screenY':screen_y}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://nopecha.com/captcha/perimeterx')
    parser.add_argument('--timeout', type=float, default=30)
    profile_source=parser.add_mutually_exclusive_group()
    profile_source.add_argument('--profile', choices=('linux','default'), default='linux')
    profile_source.add_argument('--profile-file', type=Path, help='Use an explicit generated fingerprint profile')
    parser.add_argument('--shell', type=Path)
    proxy_source=parser.add_mutually_exclusive_group()
    proxy_source.add_argument('--proxy-env', metavar='NAME', help='Read the proxy URL from an environment variable; report only whether a proxy was used')
    proxy_source.add_argument('--proxy-file', type=Path, help='Read one proxy URL per line from a local file')
    parser.add_argument('--proxy-index', type=int, default=1, help='Select a proxy from the file, counting from 1')
    parser.add_argument('--proxy-as-http', action='store_true', help='Explicitly use the HTTP CONNECT endpoint of a proxy gateway that also supports SOCKS')
    parser.add_argument('--hold', type=float, default=0, help='Send trusted down/up with the event loop running between them')
    parser.add_argument('--until-complete', action='store_true', help='Release when the widget asks to wait; --hold is the maximum duration')
    parser.add_argument('--accessible', action='store_true', help='Use the displayed two-click accessibility flow')
    parser.add_argument('--observe-server-result', action='store_true',
                        help='Instrument the PX764 result callback, forwarding it unchanged; diagnostic results are not clean acceptance evidence')
    parser.add_argument('--wait', type=float, default=0, help='Wait this many seconds after the widget appears before interacting')
    parser.add_argument('--check-walmart', action='store_true', help='After interacting, fetch the Walmart homepage in the same session and record its HTTP status/title')
    parser.add_argument('--output', type=Path, default=Path('artifacts/perimeterx/probe.json'))
    args = parser.parse_args()
    if (not math.isfinite(args.timeout) or args.timeout<=0 or not math.isfinite(args.hold) or
            not 0<=args.hold<=60 or not math.isfinite(args.wait) or not 0<=args.wait<=60):
        parser.error('timeout must be positive; hold and wait must be between 0 and 60 seconds')
    if args.until_complete and not args.hold:
        parser.error('--until-complete requires a positive --hold maximum')
    proxy=os.environ.get(args.proxy_env) if args.proxy_env else None
    if args.proxy_env and not proxy:
        parser.error('--proxy-env must name a nonempty environment variable')
    if args.proxy_index<1 or args.proxy_index!=1 and not args.proxy_file:
        parser.error('--proxy-index must select a line in --proxy-file, counting from 1')
    if args.proxy_file:
        proxies=[line.strip() for line in args.proxy_file.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        if args.proxy_index>len(proxies):parser.error('--proxy-index exceeds the number of proxies in the file')
        proxy=proxies[args.proxy_index-1]
    original_proxy=proxy
    if args.proxy_as_http:
        if not proxy:parser.error('--proxy-as-http requires a proxy')
        address=urlsplit(proxy)
        proxy=urlunsplit(('http',address.netloc,address.path,address.query,address.fragment))
    bindings = load_bindings()
    profile=json.loads(args.profile_file.read_text()) if args.profile_file else _linux_profile() if args.profile=='linux' else None
    if profile is not None and not isinstance(profile,dict):parser.error('profile file must contain a JSON object')
    report = {'url':public_url(args.url),'profile':'file' if args.profile_file else args.profile,'proxy':bool(proxy),'verified':False,
              'outcome':'unconfirmed','errors':[],'states':[],'input':[],'progress':[]}
    if args.observe_server_result:report['instrumented']=True
    if args.profile_file:report['profile_sha256']=hashlib.sha256(args.profile_file.read_bytes()).hexdigest()
    started=monotonic()
    with bindings.Context(shell=args.shell,timeout=30,proxy=proxy,tls='159') as context:
        context.configure_clock('system')
        if profile is not None:context.configure_fingerprint(profile)
        context.navigate(args.url)
        last=None
        held=None
        hold_started=None
        released=False
        ready_since=None
        accessible_started=False
        confirm_since=None
        target_state={}
        backend_window='window'
        try:
            while monotonic()-started<args.timeout:
                context.timeout=min(15,max(0.5,args.timeout-(monotonic()-started)))
                try:context.advance(100,max_tasks=400)
                except bindings.JSError as error:
                    record={'name':error.name,'message':error.message,'stack':error.stack}
                    if record not in report['errors']:report['errors'].append(record)
                state=None
                if held is not None and not released:
                    target_state=json.loads(context.eval('JSON.stringify('+TARGET_STATE+')'))
                    if not report['progress'] or report['progress'][-1]['state']!=target_state:
                        report['progress'].append({'elapsed':round(monotonic()-started,3),'state':target_state})
                else:
                    source=CAPTURE_STATE if args.hold or args.accessible else STATE
                    state=json.loads(context.eval('JSON.stringify('+source+')'))
                    for root in state['roots']:
                        if root['type']==9 and any(e.get('id')=='px-captcha' for e in root['elements']):
                            backend_window='('+root['path']+').defaultView'
                            if args.observe_server_result:
                                context.eval(OBSERVE_SERVER_RESULT+'('+backend_window+')')
                    for document in state['documents']:
                        document['url']=public_url(document['url'])
                        document['scripts']=[public_url(url) for url in document['scripts']]
                    if state!=last:
                        report['states'].append({'elapsed':round(monotonic()-started,3),'state':state})
                        print(json.dumps(report['states'][-1],ensure_ascii=False),flush=True)
                        last=state
                    if ready_since is None and any(b.get('aria') in ('Accessible challenge','Press & Hold Human Challenge')
                                                  for b in state['buttons'] if b['visible']):
                        ready_since=monotonic()
                    alerts=[e.get('text','') for root in state['roots'] for e in root['elements']
                            if e.get('role')=='alert']
                    if any('please try again' in text.lower() for text in alerts):
                        report['outcome']='rejected'
                        break
                ready=ready_since is not None and monotonic()-ready_since>=args.wait
                if args.accessible and state is not None and ready:
                    if not accessible_started and any(b['visible'] and b.get('aria')=='Accessible challenge' for b in state['buttons']):
                        context.click('globalThis.__ruyiPxCandidates.accessible',hold_ms=110)
                        accessible_started=True
                        report['input'].append({'event':'accessible_start','elapsed':round(monotonic()-started,3)})
                    if accessible_started and not released and any(b['visible'] and b.get('aria')=='Press again' for b in state['buttons']):
                        if confirm_since is None:confirm_since=monotonic()
                        elif monotonic()-confirm_since>=0.4:
                            context.click('globalThis.__ruyiPxCandidates.confirm',hold_ms=110)
                            released=True
                            report['input'].append({'event':'accessible_confirm','elapsed':round(monotonic()-started,3)})
                if args.hold and not args.accessible and ready and held is None and state is not None:
                    candidates=[button for button in state['buttons'] if button.get('visible') and
                        'Press & Hold' in (button.get('aria') or '') and button['rect']['width']>0]
                    if candidates:
                        held=candidates[0]
                        context.eval('globalThis.__ruyiHeldTarget=globalThis.__ruyiPxCandidates.hold;true')
                        held['path']='globalThis.__ruyiHeldTarget'
                        rect=held['rect']
                        screen=context.eval('(()=>{const w=('+held['path']+').ownerDocument.defaultView;return [w.mozInnerScreenX,w.mozInnerScreenY]})()')
                        init={**input_point(rect,screen),'button':0,'buttons':0,'bubbles':True,
                              'composed':True,'cancelable':True,'detail':1}
                        pointer={'pointerId':0,'pointerType':'mouse','isPrimary':True,'width':1,'height':1}
                        for event in ('pointerover','pointerenter','mouseover','mouseenter','pointermove','mousemove','pointerdown','mousedown'):
                            fields={**init,**(pointer if event.startswith('pointer') else {})}
                            if event in ('pointerover','pointerenter','pointermove'):fields['button']=-1
                            if event in ('pointerenter','mouseenter'):
                                fields['bubbles']=False
                                fields['cancelable']=False
                            fields['detail']=1 if event=='mousedown' else 0
                            if event in ('pointerdown','mousedown'):fields['buttons']=1
                            if event.startswith('pointer'):fields['pressure']=0.5 if fields['buttons'] else 0
                            context.dispatch_input(event,target=held['path'],**fields)
                            if event=='mousedown':context.eval('globalThis.__ruyiHeldTarget.focus();true')
                        hold_started=monotonic()
                        report['input'].append({'event':'down','elapsed':round(monotonic()-started,3)})
                complete='completed' in (target_state.get('aria') or '').lower() or target_state.get('aria')=='Please wait'
                if held is not None and not released and (monotonic()-hold_started>=args.hold or args.until_complete and complete):
                    for event in ('pointerup','mouseup','click'):
                        fields={**init,**(pointer if event.startswith('pointer') or event=='click' else {})}
                        fields['buttons']=0
                        fields['detail']=0 if event=='pointerup' else 1
                        if event in ('pointerup','click'):fields['pressure']=0
                        context.dispatch_input(event,target=held['path'],**fields)
                    released=True
                    if complete:report['outcome']='client_completed'
                    report['input'].append({'event':'up','elapsed':round(monotonic()-started,3)})
        except Exception as error:
            report['outcome']='error'
            report['error']=f'{type(error).__name__}: {error}'
        if not context.closed:
            if args.observe_server_result:
                report['server_results']=json.loads(context.eval('JSON.stringify(globalThis.__ruyiPxServerResults||[])'))
            if args.check_walmart:
                context.timeout=20
                try:
                    context.eval(r'''globalThis.__ruyiPxBackend=null;
                        FETCH('https://www.walmart.com/',{credentials:'include'})
                          .then(async r=>{const html=await r.text();
                            const title=(html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)||[])[1]||'';
                            __ruyiPxBackend={status:r.status,title:title.slice(0,200),url:r.url,
                              blocked:/robot or human\?/i.test(title)||new URL(r.url).pathname==='/blocked'};})
                          .catch(e=>__ruyiPxBackend={error:String(e)});true'''.replace('FETCH',backend_window+'.fetch'))
                    deadline=monotonic()+20
                    while monotonic()<deadline:
                        context.advance(100,max_tasks=200)
                        result=json.loads(context.eval('JSON.stringify(__ruyiPxBackend)'))
                        if result is not None:
                            if 'url' in result:result['url']=public_url(result['url'])
                            report['protected_page']=result
                            break
                    else:report['protected_page']={'error':'response timeout'}
                except Exception as error:report['protected_page']={'error':f'{type(error).__name__}: {error}'}
            context.timeout=5
            report['network']=[{key:(public_url(row[key]) if key=='url' else row[key])
                for key in ('method','url','status','error') if key in row} for row in context.net_log()]
            report['console']=[{key:row[key] for key in ('level','text','realm') if key in row}
                               for row in context.console_log() if row.get('level') in ('error','warn')]
    report['elapsed']=round(monotonic()-started,3)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(report,indent=2,ensure_ascii=False)
    if proxy:encoded=encoded.replace(proxy,'[proxy omitted]')
    if original_proxy:encoded=encoded.replace(original_proxy,'[proxy omitted]')
    args.output.write_text(encoded+'\n')
    print(json.dumps({'elapsed':report['elapsed'],'outcome':report['outcome'],'verified':report['verified'],'errors':len(report['errors']),
                      'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
