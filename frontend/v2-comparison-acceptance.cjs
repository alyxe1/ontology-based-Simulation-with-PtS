// Run after scripts.v2_comparison_acceptance, against its isolated service.
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const port=Number(process.env.ONTOLOGYSIM_UI_ACCEPTANCE_PORT||8848);
if(port===8820)throw Error('Use the isolated comparison acceptance service');

(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const button=(name)=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:'+port);
  await page.getByLabel('账号',{exact:true}).fill('comparison-test');
  await page.getByLabel('密码',{exact:true}).fill('isolated-comparison-test-only');
  await button('登录').click();
  await page.locator('nav').getByRole('button',{name:'异常与方案',exact:true}).click();
  const baselines=page.getByLabel('分析基线',{exact:true});
  await baselines.locator('option').filter({hasText:'CNC01 刀具维护'}).waitFor({state:'attached'});
  await baselines.selectOption({label:await baselines.locator('option').filter({hasText:'CNC01 刀具维护'}).innerText()});
  await page.getByLabel('方案模板').selectOption('custom');
  await button('添加方案').click();
  await page.getByText('指定等待工件的目标设备',{exact:false}).click();
  const jobs=page.getByLabel('工件',{exact:true});
  const options=await jobs.locator('option').allTextContents();
  const p2=options.find(option=>option.includes('WO002')&&option.includes('轮廓铣削'));
  assert(p2,'Expected a queued P2 workpiece affected by CNC01 maintenance');
  await jobs.selectOption({label:p2});
  const target=page.getByLabel('目标设备');
  await target.selectOption('CNC02');
  assert.equal(await target.locator('option[value="CNC03"]').count(),0);
  await button('确认指派').click();
  assert(await button('运行比较').isDisabled());
  await button('校验方案').click();
  await button('运行比较 · 2 个任务').waitFor();
  await page.getByLabel('等待队列顺序').selectOption('EDD');
  assert(await button('运行比较').isDisabled(),'Editing a validated draft must require revalidation');
  // The six-piece fixture permits a quick visible COM run initiated by the Web UI.
  await baselines.selectOption({label:await baselines.locator('option').filter({hasText:/^分析基线 ·/}).first().innerText()});
  await page.getByLabel('方案模板').selectOption('shift');
  await button('添加方案').click();
  await button('校验方案').click();
  await button('运行比较 · 2 个任务').click();
  await page.getByText('方案已排队，Plant Simulation 将逐个打开并执行').waitFor();
  await page.getByRole('heading',{name:'原生核对证据'}).waitFor({timeout:120000});
  const groups=page.getByLabel('比较批次');
  assert.equal(await groups.locator('option').count(),2);
  await groups.selectOption({index:1});
  await page.getByRole('cell',{name:'自定义交期优先',exact:true}).first().waitFor();
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,manual_assignment:true,validation_invalidated_on_edit:true,
   native_started_from_browser:true,comparison_groups:2}));
 }finally{await browser.close()}
})().catch(err=>{console.error(err);process.exitCode=1});
