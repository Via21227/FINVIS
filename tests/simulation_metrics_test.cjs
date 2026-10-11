/* Numeric presentation counterexamples. No DOM, no storage. */
const fs=require('fs'),assert=require('assert');
(async()=>{const {spendingProportion:ratio,composition}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('web/simulation_metrics.js')).toString('base64'));
const a=(l,u=l)=>({lower_minor:l,upper_minor:u}),base={currency:'CNY',starting:a(3400000),known_cost:a(600000),known_inflow:a(1000000),unknown_count:0,unsupported_count:0};let count=0;const test=(name,fn)=>{fn();count++;console.log('PASS '+name)};
test('spending divided by original funds',()=>assert.equal(ratio(base).lower,100*600000/3400000));
test('income not denominator',()=>assert.deepEqual(ratio({...base,known_inflow:a(9900000)}),ratio(base)));
test('income not netted numerator',()=>assert.ok(ratio(base).lower>0));
test('reserved funds not deducted',()=>assert.deepEqual(ratio({...base,reserved:a(2000000)}),ratio(base)));
test('unknown not zero or fabricated into cost',()=>assert.deepEqual(ratio({...base,unknown_count:8}),ratio(base)));
for(const [label,value] of [['missing',null],['zero',a(0)],['negative',a(-1)],['cross-zero range',a(-1,10)],['bad range',a(10,1)]])test(label+' denominator omitted',()=>assert.equal(ratio({...base,starting:value}),null));
test('over 100 unbounded',()=>assert.equal(ratio({...base,known_cost:a(6800000)}).upper,200));
test('range endpoints no midpoint',()=>assert.deepEqual(ratio({...base,starting:a(100,200),known_cost:a(50,100)}),{lower:25,upper:100}));
test('unsupported effects omitted',()=>assert.equal(ratio({...base,unsupported_count:1}),null));
test('stale omitted',()=>assert.equal(ratio({...base,unavailable_reason:'stale'}),null));
test('no cost no meaningless zero bar',()=>assert.equal(ratio({...base,known_cost:a(0)}),null));
const item=(n,c='CNY')=>({currency:c,effect_type:'cost',time_basis:'one_time',amount:a(n)});
for(const n of [1,2])test(n+' rows no composition',()=>assert.equal(composition(Array.from({length:n},()=>item(100)),'CNY').size,0));
const three=[item(100),item(200),item(300)];test('three eligible rows composition',()=>assert.equal(composition(three,'CNY').size,3));test('composition exact shares',()=>assert.equal(composition(three,'CNY').get(three[2]),50));test('unknown does not reach threshold',()=>assert.equal(composition([item(100),item(200),{...item(0),amount:null}],'CNY').size,0));test('range no inferred midpoint',()=>assert.equal(composition([item(100),item(200),{...item(0),amount:a(10,30)}],'CNY').size,0));test('no cross currency ranking',()=>assert.equal(composition([item(100),item(200),item(500,'EUR')],'CNY').size,0));test('inflow not spending share',()=>assert.equal(composition([item(100),item(200),{...item(500),effect_type:'expected_inflow'}],'CNY').size,0));test('recurring excluded',()=>assert.equal(composition([item(100),item(200),{...item(500),time_basis:'monthly'}],'CNY').size,0));console.log(count+' presentation checks passed');})().catch(e=>{console.error(e);process.exitCode=1});
