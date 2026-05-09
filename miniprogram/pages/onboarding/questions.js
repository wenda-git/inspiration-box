/**
 * 灵感果仓 健康画像问卷
 *
 * 设计原则（C 方案下）：
 *   - 健康方向已在上一步选好，这里不重复问目标
 *   - 每一题都必须**真正影响配单**，不要为了凑问卷而问
 *   - 一屏一题，轻量
 *
 * 采集到的信号怎么用：
 *   gender / height / weight       → BMI → 覆盖方向 hard_rules
 *   symptoms（症状）                → selector 的 tags 打分权重
 *   veg_freq / sleep / exercise     → selector 打分（纤维 / 花青素 / 钾）
 *   taste_prefer                    → selector 品种倾向
 *   has_allergies / allergies       → 硬性过滤
 *   prenatal_stage（仅孕产方向）     → hard_rules.by_stage 分段规则
 */

const QUESTIONS = [
  {
    id: 'gender', key: 'gender', type: 'single_circle',
    title: '你的性别？',
    why: '男女每日对铁、钙、叶酸、维 B 的参考摄入量差异很大，会决定我们给你强化哪类营养。',
    options: [
      { value: 'male',   label: '男' },
      { value: 'female', label: '女' },
    ],
  },
  {
    id: 'height', key: 'height_cm', type: 'number',
    title: '你的身高？',
    why: '身高和体重一起算 BMI，是调整一周份量和糖分上限的关键依据。',
    placeholder: '请输入身高',
    unit: 'cm', min: 120, max: 220,
  },
  {
    id: 'weight', key: 'weight_kg', type: 'number',
    title: '你的体重？',
    why: '体重和身高一起算 BMI。偏瘦会放宽糖分限制、肥胖会自动收紧。',
    placeholder: '请输入体重',
    unit: 'kg', min: 30, max: 200,
    hint_jin: true,
  },
  {
    id: 'direction', key: 'direction', type: 'single',
    title: '你主要想改善什么？',
    subtitle: 'AI 会按这个方向给你挑水果',
    why: '不同方向对应不同的营养硬约束。减脂控糖会限制 GI 和糖；孕产方向强化叶酸和铁；抗氧方向提高花青素和维 C。',
    options: [
      { value: 'fatloss_gi', label: '减脂 / 控糖' },
      { value: 'antiox',     label: '抗氧 / 抗老 / 免疫' },
      // 孕产方向只对女性显示
      { value: 'prenatal',   label: '孕期 / 备孕 / 哺乳期',
        hidden_if: (f) => f.gender !== 'female' },
    ],
  },
  {
    id: 'prenatal_stage', key: 'prenatal_stage', type: 'single',
    title: '你处于哪个阶段？',
    why: '备孕到哺乳，不同阶段分别需要叶酸、铁钙、控糖或高蛋白，搭配重点完全不同。',
    // 只有女性 + 选了孕产方向的用户才会看到这题
    show_if: (f) => f.direction === 'prenatal' && f.gender === 'female',
    options: [
      { value: 'trying',      label: '备孕' },
      { value: 'first_tri',   label: '孕早期' },
      { value: 'second_tri',  label: '孕中期' },
      { value: 'third_tri',   label: '孕晚期' },
      { value: 'postpartum',  label: '哺乳期' },
    ],
  },
  {
    id: 'symptoms', key: 'symptoms', type: 'multi', max: 6,
    title: '最近有以下哪些信号？',
    subtitle: '可多选，最多 6 项',
    why: '每个信号对应特定营养缺口。疲劳多和铁/B族相关，掉发常涉及锌，熬夜暗沉可以靠花青素修复。',
    options: [
      { value: 'fatigue',      label: '容易疲劳' },
      { value: 'insomnia',     label: '睡眠差' },
      { value: 'hair_loss',    label: '掉发' },
      { value: 'constipation', label: '便秘' },
      { value: 'dull_skin',    label: '皮肤暗沉' },
      { value: 'high_glucose', label: '血糖偏高' },
      { value: 'high_pressure',label: '血压偏高' },
      { value: 'high_uric',    label: '尿酸偏高' },
      { value: 'easy_cold',    label: '容易感冒' },
      // 女性限定
      { value: 'period_discomfort', label: '经期不适',
        hidden_if: (f) => f.gender !== 'female' },
      { value: 'none',         label: '没有以上信号' },
    ],
  },
  {
    id: 'veg_freq', key: 'veg_freq', type: 'single',
    title: '平时吃蔬菜多吗？',
    why: '蔬菜摄入少会导致膳食纤维和维生素不足，我们会用高纤维、多色水果来补。',
    options: [
      { value: 'almost_none', label: '几乎不吃' },
      { value: 'light',       label: '每周 1–3 次' },
      { value: 'moderate',    label: '每天都有一点' },
      { value: 'plenty',      label: '每餐都有' },
    ],
  },
  {
    id: 'sleep_pattern', key: 'sleep_pattern', type: 'single',
    title: '平时几点睡？',
    why: '长期熬夜会加速氧化压力，花青素（蓝莓、樱桃）和维 C 是天然的修复组合。',
    options: [
      { value: 'regular',   label: '12 点前睡' },
      { value: 'late',      label: '1–2 点睡' },
      { value: 'very_late', label: '2 点后睡' },
      { value: 'shift',     label: '倒班 / 夜班' },
    ],
  },
  {
    id: 'exercise_freq', key: 'exercise_freq', type: 'single',
    title: '一周运动几次？',
    why: '运动频率决定了你对钾、快碳水和抗炎成分的需求量。',
    options: [
      { value: 'none',     label: '几乎不运动' },
      { value: 'light',    label: '偶尔走走' },
      { value: 'moderate', label: '2–3 次' },
      { value: 'heavy',    label: '4 次以上' },
    ],
  },
  {
    id: 'taste', key: 'taste_prefer', type: 'multi', max: 3,
    title: '你喜欢的口感？',
    subtitle: '可多选，最多 3 项',
    why: '营养达标之外，口味合拍才会坚持吃完。这不会改变硬指标，但会影响品种倾向。',
    options: [
      { value: 'sweet', label: '偏甜' },
      { value: 'sour',  label: '偏酸' },
      { value: 'crisp', label: '脆口' },
      { value: 'juicy', label: '多汁' },
      { value: 'aroma', label: '香气浓' },
      { value: 'mild',  label: '清淡' },
    ],
  },
  {
    id: 'has_allergies', key: 'has_allergies', type: 'single',
    title: '对水果有过敏吗？',
    why: '过敏反应比偏好严重得多，所以直接一票否决，不做任何替代或降权处理。',
    options: [
      { value: 'no',  label: '都不过敏' },
      { value: 'yes', label: '有过敏的水果' },
    ],
  },
  {
    id: 'allergies', key: 'allergies', type: 'multi_grouped', max: 20,
    title: '勾选你过敏的水果',
    subtitle: '可多选；过敏项永远不会出现在你的周箱里',
    why: '按分类列出市面常见水果，勾的都会硬性规避。',
    show_if: (f) => f.has_allergies === 'yes',
    option_groups: [
      {
        group: '浆果类',
        options: [
          { value: '蓝莓',   label: '蓝莓' },
          { value: '草莓',   label: '草莓' },
          { value: '树莓',   label: '树莓' },
          { value: '黑莓',   label: '黑莓' },
          { value: '桑葚',   label: '桑葚' },
          { value: '蔓越莓', label: '蔓越莓' },
        ],
      },
      {
        group: '柑橘类',
        options: [
          { value: '橙子',   label: '橙子' },
          { value: '柚子',   label: '柚子' },
          { value: '橘子',   label: '橘子' },
          { value: '柠檬',   label: '柠檬' },
          { value: '葡萄柚', label: '葡萄柚' },
          { value: '金桔',   label: '金桔' },
        ],
      },
      {
        group: '核果类',
        options: [
          { value: '桃',     label: '桃子' },
          { value: '油桃',   label: '油桃' },
          { value: '李子',   label: '李子' },
          { value: '樱桃',   label: '樱桃' },
          { value: '杏',     label: '杏' },
          { value: '梅子',   label: '梅子' },
        ],
      },
      {
        group: '仁果类',
        options: [
          { value: '苹果',   label: '苹果' },
          { value: '梨',     label: '梨' },
          { value: '枇杷',   label: '枇杷' },
          { value: '山楂',   label: '山楂' },
        ],
      },
      {
        group: '热带水果',
        options: [
          { value: '芒果',   label: '芒果' },
          { value: '菠萝',   label: '菠萝' },
          { value: '木瓜',   label: '木瓜' },
          { value: '火龙果', label: '火龙果' },
          { value: '百香果', label: '百香果' },
          { value: '榴莲',   label: '榴莲' },
          { value: '椰子',   label: '椰子' },
          { value: '荔枝',   label: '荔枝' },
          { value: '龙眼',   label: '龙眼' },
          { value: '山竹',   label: '山竹' },
          { value: '香蕉',   label: '香蕉' },
        ],
      },
      {
        group: '瓜果类',
        options: [
          { value: '西瓜',   label: '西瓜' },
          { value: '哈密瓜', label: '哈密瓜' },
          { value: '甜瓜',   label: '甜瓜' },
        ],
      },
      {
        group: '藤蔓类',
        options: [
          { value: '葡萄',   label: '葡萄' },
          { value: '提子',   label: '提子' },
          { value: '猕猴桃', label: '猕猴桃' },
        ],
      },
      {
        group: '其他',
        options: [
          { value: '牛油果', label: '牛油果' },
          { value: '石榴',   label: '石榴' },
          { value: '无花果', label: '无花果' },
          { value: '柿子',   label: '柿子' },
          { value: '枣',     label: '枣' },
        ],
      },
    ],
  },
];


/**
 * 根据画像计算 BMI 状态 + 用户画像摘要（展示用）
 * direction 从 form 里读（问卷第 4 题），不再依赖外部传入
 */
function buildDiagnosis(f) {
  const bullets = [];

  const h = Number(f.height_cm) || 165;
  const w = Number(f.weight_kg) || 60;
  const bmi = +(w / ((h / 100) ** 2)).toFixed(1);
  let bmiDesc;
  if (bmi < 18.5) bmiDesc = '偏瘦，保持总量 + 足够碳水';
  else if (bmi < 24) bmiDesc = '标准，常规均衡打底';
  else if (bmi < 28) bmiDesc = '超重，AI 自动收紧糖和热量';
  else bmiDesc = '肥胖区间，采用最严格控糖方案';
  bullets.push({ label: `BMI ${bmi}`, detail: bmiDesc });

  const direction = f.direction;
  const dirLabel = {
    fatloss_gi: '减脂控糖方向',
    prenatal:   '孕产营养方向',
    antiox:     '抗氧轻食方向',
  }[direction] || '个性化方向';
  const dirDetail = {
    fatloss_gi: '低 GI ≤ 45，糖 ≤ 10g，高纤维',
    prenatal:   '叶酸 ≥ 40µg/份，维 C ≥ 20mg',
    antiox:     '花青素 + 维 C + ORAC 组合',
  }[direction] || '已为你匹配最合适的硬约束';
  bullets.push({ label: dirLabel, detail: dirDetail });

  if (direction === 'prenatal' && f.prenatal_stage) {
    const stageMap = {
      trying: '叶酸重点补给', first_tri: '温和少酸',
      second_tri: '高铁高钙', third_tri: '控糖 + 高纤维',
      postpartum: '哺乳期补给',
    };
    bullets.push({ label: '孕产阶段', detail: stageMap[f.prenatal_stage] });
  }

  const symTag = {
    fatigue: '铁 + 花青素（抗疲劳）',
    insomnia: '镁（助眠）',
    hair_loss: '锌 + 维 C（护发）',
    constipation: '高纤维（助排便）',
    dull_skin: '维 C + 花青素（护肤）',
    high_glucose: '严格控 GI',
    high_pressure: '高钾 + 低糖',
    high_uric: '避开高果糖',
    easy_cold: '维 C ≥ 40mg',
    period_discomfort: '高铁 + 维 C',
  };
  const sym = (f.symptoms || []).filter(s => s !== 'none').map(s => symTag[s]).filter(Boolean);
  if (sym.length) {
    bullets.push({ label: `${sym.length} 项营养补足`, detail: sym.slice(0, 3).join('；') });
  }

  if (f.veg_freq === 'almost_none' || f.veg_freq === 'light') {
    bullets.push({ label: '蔬菜偏少', detail: '优先配富含纤维的水果' });
  }
  if (f.sleep_pattern === 'late' || f.sleep_pattern === 'very_late') {
    bullets.push({ label: '熬夜多', detail: '花青素修复视疲劳' });
  }
  if ((f.allergies || []).filter(a => a !== 'none').length) {
    const n = f.allergies.filter(a => a !== 'none').length;
    bullets.push({ label: `${n} 项过敏`, detail: '算法硬性规避' });
  }

  return {
    direction,
    plan_label: dirLabel,
    bmi,
    bullets,
    height_cm: h,
    weight_kg: w,
  };
}


function visibleQuestions(form) {
  return QUESTIONS
    .filter(q => !q.show_if || q.show_if(form))
    .map(q => {
      // 选项级动态过滤：支持 options 和 option_groups 的 hidden_if
      if (q.options) {
        const filtered = q.options.filter(opt => !opt.hidden_if || !opt.hidden_if(form));
        return { ...q, options: filtered };
      }
      if (q.option_groups) {
        const groups = q.option_groups
          .map(g => ({
            ...g,
            options: (g.options || []).filter(opt => !opt.hidden_if || !opt.hidden_if(form)),
          }))
          .filter(g => g.options.length > 0);
        return { ...q, option_groups: groups };
      }
      return q;
    });
}


module.exports = { QUESTIONS, visibleQuestions, buildDiagnosis };
