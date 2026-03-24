
 
 # 新的计划
 我想再一次打算项目的未来。

 首先修好我们的Bugs。 
# Bugs

- 没有故事的话，提示换模型 check if this is already implemented
        
- DeepSeek当默认？不在用Haiku为了找HSK水平等 maybe this is also already implemented

- Issue: 查看是否zhibo (this chinese model) 能不能用。能的话，我要用这个免费的模型做那个简单的任务，比如HSK-水平等

- ai: generate_story: 53 cards: ['网购', '乱', '炸小吃', '兆', '赞许', '随意', '晚年', '顺理成章', '落空', '堂', '学名', '小名', '顺口', '奎', '沫', '省事', '响亮', '典故', '谐音', '迫不及待', '囊中羞涩', '免不了', '货币', '俗', '类似', '风尚', '伦理', '情感', '无所不谈', '旧社会', '滑稽', '啼哭', '过失', '喜气洋洋', '赌气', '三流', '不谋而合', '留心', '注册', '商标', '专利', '梓', '分辨', '不以为然', '鉴于', '雷同', '撞车', '特定', '挂钩', '货色', '信赖', '过早的思念', '繁体字'] 不要表示所有的词 - by that I mean in the terminal logging I just don't want to see every vocab that was sent because it just clutters the terminal. I want to log information precise, but important

- 再次或者生成故事前warn我如果还有again的卡

- 更大的学习设置
    meaning the dropdown menues. Right now I just cannot read what I selected.

- 句子的数据（上面左边）不对。但是我也不明白，这怎么用。This is an issue where the problem first has to be investigated again. It's not the most important thing right now
        为什么新卡排序“card type”没有我认为的行为？   
        我正在学习Chapter 2的综合牌组。激活了random卡顺序吗
        所以他给我表达的排序，不是他给AI发的排序？因此句子的排序不对？
        我们可以调整这个行为吗？给AI发和给我表达的排序我想要一样的。
            日期一样，就随机选卡片没问题，但是然后我要发给AI前完成这个过程，因此顺序stays the same after that.明白吗
 
# Feature Request:

- 导入卡片的时候，可以取消孤独卡片 （在表格里选择“-”还是图标，然后城改“+”图标，选择这一个的意思就是再加卡片。有问题的卡不可以选择“+”（比如格式不对））

- 按下生词比如在听/读的卡，可以选择：在创建（create）的类似unsuspend. 然后如果在一个卡牌只一个卡没有suspended,我们就必须改变那个|| 和 〉按钮的模式。我们改变像bury all的按钮一样。
- directly monitor Retention Rate etc. 关于一个卡片，也关于牌组
- 完成牌组的时候，不想要看那个“All Done”。他要给我again的卡片的时间的表格。

- 生成故事的时候，我也想可以选“不生成新故事，把另外一个牌组的句子，没有的话就给我单独词汇
- completely create missing fields for a card with AI. So I just have to give it vocab and it fills out the rest。这个能力我想已经导入的时候可以选择
    -比如给他这个生词：便捷
        赞不绝口
        花样
        价位
        实惠
        区域

- click on a sentence (generated or not) and let an AI explain this sentence and it's grammars. This explanation should be safed with the sentence. All generated sentences should be actually safed somewhere in the DB already...
    - 然后我也要这个功能，可以按下句子的汉子，然后开心的窗，可以看这个次的信息，如果这个汉子是词的一部分，可以选择还是看两个（UI需要很方便游泳）。这样我想越来越创建自己的词典。没有entry的话就可以用AI fill out 没有的信息
    - 因此你hover over句子或者汉子或词，可以选择看那个
    - 然后也可以加生词当要学习的生词

- on the side 我想要一个表格，给我看还有again 卡片和他们还需要的时间再给我看 ofc not showing which card, but an anonym table。

- have a clock for each card that counts just how long in totatl (front and backside ) was looked at the cardR

- 然后想介绍最重要的功能。为了学习汉语得学习汉子，但是也要学习satzkonstruktionen/语法，比如怎么用那个 “所。。。的“的结构。然后我想象把这些结构当作我们普通的卡片差不多，可是他们也并不一样。我们叫他们 结构笔记（notes）。然后像传统笔记一样会有阅读，听，创造的卡。然后他们不在分配到牌组。上面就有心的按钮叫“结构”。然后每个牌组会有一个按钮，可以启用和禁用结构功能。但是结构功能是什么？
    当复习卡片时，复习前我们不仅把生词发给AI，而且也用一个语法结构发给AI。然后 AI必须在句子里用此生词和这个结构。然后我们必须判断我们对生词的好不好（again, hard,...) 但是也必须判断对结构怎么样。这个对创造的类似印象最大。
    比一般的生词卡最大的区别也是：一个结构可以在一个牌组的故事好几次。越学好越少发生。但是我们还一起必须思考一个这个模式的具体：多少次出现一个结构？判断的印象怎么样？句子没有固定的结构多少？我想和你一起讨论这些问题。
    Here we also have to think about how the yaml files should be structured from now on. I already have some ideas. But we can discuss this later too





# Ideas





- Open Claw and Signal to collect sentences and words?



- 	Add texts to read (newspaper/schoolbook)

