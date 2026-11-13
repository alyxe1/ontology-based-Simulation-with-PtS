// Run against the isolated comparison acceptance workspace with two completed batches.
const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
const port=Number(process.env.ONTOLOGYSIM_UI_ACCEPTANCE_PORT||8848);
if(port===8820)throw Error('Use an isolated comparison acceptance service');

(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const duplicateKeys=[];
  page.on('console',message=>{
   if(/two children with the same key/i.test(message.text()))duplicateKeys.push(message.text());
  });
  const nav=title=>page.locator('nav').getByRole('button',{name:title,exact:true});
  const seal=page.getByRole('button',{name:'封存本批比较',exact:true});
  const panel=page.getByRole('heading',{name:'形成闭环档案',exact:true});
  await page.goto('http://127.0.0.1:'+port);
  await page.getByLabel('账号',{exact:true}).fill('comparison-test');
  await page.getByLabel('密码',{exact:true}).fill('isolated-comparison-test-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await nav('异常与方案').click();
  await expect(seal).toHaveCount(1,{timeout:30000});
  const groups=page.getByLabel('比较批次',{exact:true});
  const ids=await groups.locator('option').evaluateAll(options=>options.map(option=>option.value));
  assert(ids.length>=2,'The acceptance workspace needs at least two completed batches');
  for(const id of [ids[1],ids[0],ids[1],ids[0]]){
   await groups.selectOption(id);
   await expect(page.getByRole('heading',{name:'方案结果分析',exact:true})).toBeVisible();
   await expect(seal).toHaveCount(1);
   await expect(panel).toHaveCount(1);
  }
  for(const title of ['生产运行','产品与工艺','数据与语义','证据与回放','系统管理']){
   await nav(title).click();
   await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible();
   await expect(seal).toHaveCount(0);
   await expect(panel).toHaveCount(0);
  }
  await nav('异常与方案').click();
  await expect(seal).toHaveCount(1);
  await page.getByLabel('分析基线',{exact:true}).selectOption({index:0});
  await expect(seal).toHaveCount(0);
  await expect(panel).toHaveCount(0);
  assert.deepEqual(duplicateKeys,[]);
  console.log('Comparison batch switching, all six workspaces and clearing the baseline passed');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
