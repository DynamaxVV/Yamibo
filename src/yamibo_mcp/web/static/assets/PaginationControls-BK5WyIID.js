import{c,u as N,r as a,j as n,d as k}from"./index-B648pbTS.js";/**
 * @license lucide-react v1.47.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const f={name:"chevron-left",size:24,node:[["path",{d:"m15 18-6-6 6-6",key:"1wnfg3"}]]};f.node;const w=c(f);/**
 * @license lucide-react v1.47.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const g={name:"chevron-right",size:24,node:[["path",{d:"m9 18 6-6-6-6",key:"mthhwq"}]]};g.node;const _=c(g);/**
 * @license lucide-react v1.47.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const p={name:"chevrons-left",size:24,node:[["path",{d:"m11 17-5-5 5-5",key:"13zhaf"}],["path",{d:"m18 17-5-5 5-5",key:"h8a8et"}]]};p.node;const C=c(p);/**
 * @license lucide-react v1.47.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const v={name:"chevrons-right",size:24,node:[["path",{d:"m6 17 5-5-5-5",key:"xnjwq"}],["path",{d:"m13 17 5-5-5-5",key:"17xmmf"}]]};v.node;const z=c(v);function D({page:e,totalPages:o,onPageChange:y,className:j,scrollTargetId:m,suppressScrollRef:r}){const{t:d,tx:u}=N(),[b,i]=a.useState(String(e)),l=a.useRef(e);a.useEffect(()=>{i(String(e))},[e]),a.useEffect(()=>{if(m&&l.current!==e){if(l.current=e,r!=null&&r.current){r.current=!1;return}window.requestAnimationFrame(()=>{const t=document.getElementById(m);t==null||t.scrollIntoView({behavior:"smooth",block:"start"})})}},[e,m,r]);const s=t=>{t!==e&&y(t)},x=()=>{const t=parseInt(b,10);if(!Number.isFinite(t)){i(String(e));return}const h=Math.min(Math.max(1,t),o);i(String(h)),s(h)};return o<=1?null:n.jsxs("div",{className:k("flex items-center justify-between gap-2 py-3 px-1 text-xs font-mono text-muted-foreground select-none",j),children:[n.jsxs("div",{className:"flex items-center gap-1",children:[n.jsx("button",{disabled:e<=1,onClick:()=>s(1),className:"p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors",title:u("第一页","First page"),children:n.jsx(C,{className:"w-3.5 h-3.5"})}),n.jsx("button",{disabled:e<=1,onClick:()=>s(e-1),className:"p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors",title:d("prev_page"),children:n.jsx(w,{className:"w-3.5 h-3.5"})}),n.jsxs("span",{className:"px-2.5 py-1 text-xs font-medium text-foreground bg-muted/50 rounded-sm border border-border/60",children:[n.jsx("span",{className:"font-semibold text-yamibo-burgundy dark:text-yamibo-coral",children:e})," / ",o]}),n.jsx("button",{disabled:e>=o,onClick:()=>s(e+1),className:"p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors",title:d("next_page"),children:n.jsx(_,{className:"w-3.5 h-3.5"})}),n.jsx("button",{disabled:e>=o,onClick:()=>s(o),className:"p-1 rounded-sm border border-border bg-card hover:bg-muted text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors",title:u("最后一页","Last page"),children:n.jsx(z,{className:"w-3.5 h-3.5"})})]}),n.jsxs("div",{className:"flex items-center gap-1.5",children:[n.jsx("span",{className:"text-[11px] text-muted-foreground/80",children:d("jump_to_page")}),n.jsx("input",{className:"w-12 text-center text-xs font-mono py-0.5 px-1 rounded-sm border border-border bg-card text-foreground focus:outline-none focus:ring-1 focus:ring-yamibo-burgundy",inputMode:"numeric",value:b,onChange:t=>i(t.target.value.replace(/[^\d]/g,"")),onKeyDown:t=>{t.key==="Enter"&&x()}}),n.jsx("button",{onClick:x,className:"px-2 py-0.5 rounded-sm border border-border bg-card hover:bg-muted text-foreground text-[11px] font-sans transition-colors press-feedback",children:d("go_page")})]})]})}export{D as P};
