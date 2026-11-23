import { chromium } from "@playwright/test";
const b=await chromium.launch({headless:true}); const p=await b.newPage();
const errs=[]; p.on("pageerror",e=>errs.push(e.message)); p.on("console",m=>{if(m.type()==="error")errs.push("C:"+m.text());});
await p.goto("http://127.0.0.1:8800/",{waitUntil:"load"});
await p.getByText("one_man_corp 직원 상태창").waitFor({timeout:15000});
// 텍스트뷰 기본 + 이미지 토글 ON
try{ await p.getByTestId("image-toggle").click({timeout:8000}); }catch(e){ console.log("toggle?",String(e).slice(0,60)); }
await p.waitForTimeout(2000);
await p.screenshot({path:"../results/phase4c/text_portrait.png", fullPage:false});
const sp = await p.getByTestId("speaker-portrait").count().catch(()=>0);
console.log("speaker-portrait count:", sp, "errs:", errs.length, JSON.stringify(errs.slice(0,4)));
await b.close();
