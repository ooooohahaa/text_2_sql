# 游戏数据库 Schema 完整描述（GraphRAG / Text2SQL 用）

本文档描述数据库 DB_SPLAN_00 中所有表所代表的业务实体及各字段含义，适用于基于 LLM 的 Text2SQL 与 GraphRAG 检索。游戏为多服架构，**玩家唯一标识**通常由 `userid`（用户ID）+ `svrid`（服务器ID）共同确定；部分表使用 `userId`/`serverId` 命名，含义相同。

---

## 一、用户与账号

### 1. user_base_info_table_0（用户基础信息表）

**实体含义**：每个游戏角色在某个服务器上的**基础货币与资源**。一行对应一个 (userid, svrid)，即“某服下的某账号”。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 用户ID，账号唯一标识 |
| svrid | tinyint unsigned | 用户所在服务器ID |
| coin | int unsigned | 赛尔豆（游戏内基础货币） |
| diamond | int unsigned | 钻石（高级货币，总存量） |
| freeDiamond | int unsigned | 免费获得的钻石数量 |
| payDiamond | int unsigned | 付费购买的钻石数量 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`)，即同一服下每个账号一行，按账号+区服快速定位。

---

### 2. user_info_table_0（用户扩展属性表）

**实体含义**：玩家的**键值对形式扩展属性**，如各类开关、计数、配置等。同一玩家可有多种 type，每种 type 对应一个 value（JSON）。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| type | varchar(128) | 属性类型标识，如功能开关、计数类型等 |
| value | text | 属性值，JSON 格式存储 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `type`)，即同一玩家在同一服下、同一 type 只有一条记录，便于按玩家+服+类型精确查找或更新。

---

### 3. nick_info_table_0（昵称与账号信息表）

**实体含义**：玩家的**登录与展示用账号信息**，包括昵称、外部账号、封禁状态、GM 权限等。一个 userid 一条记录，昵称全局唯一。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| nick | varchar(32) | 玩家昵称，全局唯一 |
| userid | int unsigned | 用户ID，主键 |
| account | varchar(32) | 外部账号（如渠道账号） |
| stat | int unsigned | 玩家状态：0 正常，>0 表示封禁等 |
| statStartTime | timestamp | 状态生效开始时间 |
| statEndTime | timestamp | 状态失效时间 |
| accRegTime | timestamp | 外部账号原始注册时间 |
| createTime | timestamp | 角色创建时间 |
| creatIp | varchar(32) | 创建时 IP |
| isGameMgr | int unsigned | 游戏管理员：0 否，1 GM，2 正式管理员，3 实习管理员 |
| modifiedTime | timestamp | 最后修改时间 |

---

## 二、战队

### 4. team_base_info_table_0（战队基础信息表）

**实体含义**：一个**战队**的静态信息与资源汇总。由队长创建，有名称、图标、等级、能源、经验等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| teamId | int unsigned | 战队ID，主键 |
| teamName | varchar(16) | 战队名称 |
| iconId | int unsigned | 战队图标/标志 ID |
| level | int unsigned | 战队等级 |
| createTime | int unsigned | 战队创建时间（时间戳） |
| captainId | int unsigned | 队长用户ID |
| captainName | varchar(16) | 队长昵称 |
| notice | varchar(200) | 战队公告内容 |
| totalEnergy | int unsigned | 成员累计贡献的能源总量 |
| teamEnergy | int unsigned | 当前战队能源 |
| teamExp | int unsigned | 战队经验 |
| mechEnergy | int unsigned | 机械能量 |
| dismissFlag | tinyint | 是否已解散等标记 |

**主键 / 索引**：
- 主键：(`teamId`)，每个战队一条基础信息记录。

---

### 5. team_member_info_table_0（战队成员表）

**实体含义**：战队与成员的**归属与贡献关系**。记录某用户加入某战队的时间、职位、贡献能量、战队精灵数量等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| teamId | int unsigned | 战队ID |
| userid | int unsigned | 用户ID |
| joinTime | int unsigned | 加入战队时间（时间戳） |
| privilege | tinyint unsigned | 战队职能/职位 |
| contributedEnergy | int unsigned | 该成员贡献的能量 |
| teamPetNum | int unsigned | 该成员拥有的战队精灵数量 |

**主键**：(teamId, userid)，即同一战队下每用户一条。

---

### 6. team_applier_info_table_0（战队申请表）

**实体含义**：用户**申请加入某战队的记录**。未处理或待审批的申请。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| teamId | int unsigned | 战队ID |
| userid | int unsigned | 申请者用户ID |
| appliedTime | int unsigned | 申请时间（时间戳） |

**主键 / 索引**：
- 主键：(`teamId`, `userid`)，同一战队下同一玩家只保留一条最新申请记录。

---

### 7. team_attr_info_table_0（战队属性表）

**实体含义**：战队的**数值型属性键值对**，如各种 buff、统计项等。按 (teamId, type) 存储。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| teamId | int unsigned | 战队ID |
| type | int unsigned | 属性类型 ID |
| value | int unsigned | 属性值 |

**主键 / 索引**：
- 主键：(`teamId`, `type`)，同一战队每种属性 type 只有一条记录。

---

### 8. team_equip_info_table_0（战队装置表）

**实体含义**：战队拥有的**装置/建筑**信息，如装置类型、经验、上次升级时间等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| teamId | int unsigned | 战队ID |
| type | int unsigned | 装置类型 |
| energy | int unsigned | 装置经验 |
| levelUpLastTime | int unsigned | 装置上次升级时间 |
| stopFlag | tinyint | 停用等状态标记 |

**主键 / 索引**：
- 主键：(`teamId`, `type`)，同一战队某一类装置一条记录。

---

### 9. team_event_info_table_0（战队事件流水表）

**实体含义**：战队的**操作与事件流水**，如加入、离开、捐献等。用于日志与动态展示。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| id | int unsigned | 自增主键 |
| teamId | int unsigned | 战队ID |
| type | tinyint unsigned | 事件类型 |
| userid | int unsigned | 触发事件的账号ID |
| name | varchar(20) | 触发者名字等展示用 |
| arg1, arg2, arg3 | int unsigned | 事件参数 1/2/3 |
| time | int unsigned | 事件发生时间（时间戳） |

**索引**：按 (teamId, time) 查询近期事件。

---

## 三、精灵（宠物）相关

### 10. pet_info_table_0（精灵主表）

**实体含义**：玩家拥有的**每一只精灵**的完整养成数据。单只精灵由 (userid, svrid, getTime) 唯一标识。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 用户ID |
| svrid | int unsigned | 服务器ID |
| getTime | int unsigned | 精灵获取时间，与 userid/svrid 组成唯一标识 |
| petId | int unsigned | 精灵配置 ID（种族） |
| nick | varchar(32) | 精灵昵称 |
| lockFlag | tinyint | 是否上锁（防误操作） |
| eliteFlag | tinyint | 是否精英 |
| modelId | int unsigned | 精灵模型 ID |
| exp | int unsigned | 经验值 |
| level | tinyint | 等级 |
| nature | tinyint | 性格 |
| talent | smallint | 天赋 |
| maxHp, effortHp, effortAtk, effortDef, effortSatk, effortSdef, effortSpeed | smallint | 最大血量及各项学习力 |
| battleHp, battleAtk, battleDef, battleSatk, battleSdef, battleSpeed | int | 当前战斗用六维属性 |
| skill1Id~skill4Id | int | 技能 1~4 的配置 ID |
| skill1CrtPP~skill4CrtPP | int | 技能 1~4 当前 PP 值 |
| token1GetTime~token3GetTime | int | 刻印 1~3 的获取时间（关联刻印） |
| tokenPosNum | int | 刻印位已开放数量 |
| featureId, featureExp | int | 特性相关 ID 与经验 |
| teamTechHpLearnTimes 等 | tinyint | 战队科技学习次数（各属性） |
| achieveDone | int | 是否已记入成就系统，防重复 |
| gotWay | tinyint | 获取途径 |
| preTalent | tinyint | 使用天赋药水后的天赋 |
| expireAt | int | 有效期（时间戳） |
| schemeIdx | tinyint | 学习力方案编号 |
| hiddenFlag | int | 隐藏标记位 |
| petStatus | int | 精灵状态 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `getTime`)，即同一服下某玩家在某个获取时间的一只精灵唯一记录。常用按 (userid, svrid) 过滤，再结合 getTime 精确定位某只精灵。

---

### 11. pet_skill_info_table_0（精灵技能表）

**实体含义**：精灵已学会的**技能**。一只精灵可对应多条记录（多技能）。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 用户ID |
| svrid | int unsigned | 服务器ID |
| getTime | int unsigned | 精灵获取时间（与 pet_info 对应） |
| skillId | int unsigned | 技能 ID |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `getTime`, `skillId`)，同一只精灵的同一技能只记录一次。

---

### 12. pet_token_info_table_0（精灵刻印表）

**实体含义**：玩家拥有的**刻印**及其装备情况。刻印可装备在精灵上。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 用户ID |
| svrid | int unsigned | 服务器ID |
| tokenGetTime | int unsigned | 刻印获取时间，刻印唯一标识 |
| tokenId | int unsigned | 刻印配置 ID |
| petGetTime | int unsigned | 当前装备在该精灵上的 getTime，0 表示未装备 |
| equipFlag | int | 装备状态标记 |
| ench | int unsigned | 刻印附魔已应用数据 |
| preench | int unsigned | 刻印附魔未应用数据 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `tokenGetTime`)，每个刻印实例唯一。

---

### 13. pet_additional_battle_attr_0（精灵附加战斗属性表）

**实体含义**：来自特定玩法（如雷神之路）的**临时或永久战斗属性加成**，按精灵维度记录。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int | 玩家ID |
| svrid | int | 服务器ID |
| source | int | 加成来源，如 1 表示雷神之路 |
| getTime | int | 精灵唯一标识（对应 pet_info.getTime） |
| validity | int | 有效期（秒），-1 表示永久 |
| hp, atk, def, satk, sdef, speed | int | 附加血量、物攻、物防、特攻、特防、速度 |
| nature | int | 天赋相关 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `source`, `getTime`)，即同一来源下某玩家某只精灵的一条附加属性记录。按玩家+服+来源+精灵唯一定位。

---

### 14. egg_info_table_0（精灵蛋表）

**实体含义**：玩家拥有的**精灵蛋**，孵化前或孵化中的蛋。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| getTime | int unsigned | 精灵蛋获取时间，唯一标识 |
| eggId | int unsigned | 精灵蛋配置 ID |
| endTime | int unsigned | 孵化结束时间 |
| talent | tinyint | 初始天赋 |
| nature | tinyint | 初始性格 |
| newGet | tinyint | 是否新获得未读 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `getTime`)，同一玩家在某服的每颗精灵蛋唯一记录。

---

## 四、背包与物品

### 15. backpack_info_table_0（背包表）

**实体含义**：玩家背包内**道具的数量或扩展属性**。按 (userid, svrid, itemId, type) 存储，type 可区分数量、堆叠信息等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| itemId | int unsigned | 道具配置 ID |
| type | varchar(128) | 属性类型，如数量、子类型等 |
| value | int | 属性值（如数量） |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `itemId`, `type`)，同一玩家在某服、某道具、某 type 的背包属性唯一一条。

---

### 16. thing_table_0（事物/道具流水表）

**实体含义**：玩家某种**事物**的持有或流水记录，如任务道具、活动道具等。由 (userid, svrid, tid, type) 唯一确定一条。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int | 用户ID |
| svrid | tinyint | 服务器ID |
| tid | int | 事物/道具 ID |
| param1, param2, param3 | int | 扩展参数 |
| type | tinyint | 类型，用于区分不同用途的同一 tid |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `tid`, `type`)，同一玩家在某服、某事物/道具 ID、某用途 type 唯一记录。

---

### 17. equip_info_table_0（装备信息表）

**实体含义**：玩家的**装备方案或装备栏位**信息。equipNo 可表示方案编号，equipList 为 JSON 存储的装备列表。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userId | bigint | 玩家ID |
| serverId | int | 服务器ID |
| equipNo | int | 装备编号（如方案序号） |
| equipList | text | 装备信息，JSON |
| datetime | datetime | 更新时间 |

**主键 / 索引**：
- 主键：(`userId`, `serverId`, `equipNo`)，同一玩家在某服、某装备方案编号一条记录。

---

### 18. clothe_info_table_0（装扮表）

**实体含义**：玩家拥有的**时装/装扮**及穿戴状态。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| clotheId | int unsigned | 装扮配置 ID |
| expire | int | 过期时间，-1 表示永久 |
| state | tinyint | 0 未穿戴，1 已穿戴 |
| battle_expire | int | 附加战斗属性有效期 |
| battle_group_id | int | 附加战斗属性组 ID |
| getTime | int | 装扮实例唯一标识 |
| id | int | 配置编号 |
| replaceId | int | 复刻装扮对应的原装扮 ID |

**主键 / 索引**：
- 主键：(`getTime`, `userid`, `svrid`)，即同一服下某玩家每件装扮实例一条记录。常用按 (userid, svrid) 过滤，再结合 getTime 精确定位某件装扮。

---

## 五、商店

### 19. shop_info_table_0（商店购买记录表）

**实体含义**：玩家在**商店**中对某商品的**已购买次数**（用于限购、刷新等）。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| idx | int unsigned | 对应 shopMass 配置表中的商品 ID |
| buyNum | int unsigned | 该玩家对该商品的购买件数 |
| shopType | int unsigned | 商店类型 |
| subType | int unsigned | 商店子类型 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `idx`, `shopType`, `subType`)，同一玩家在某服、某类商店下对某商品的购买记录唯一。

---

### 20. random_shop_info_table_0（随机商店表）

**实体含义**：**随机商店**中每个格子当前刷出的商品及购买状态。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | tinyint | 游戏服ID |
| pos | tinyint | 格子位置 |
| idx | tinyint | 商品在配置表中的下标 |
| buyState | tinyint | 0 未购买，1 已购买 |
| shopType | tinyint | 商店类型 |
| itemId | int unsigned | 物品 ID |
| itemNum | int unsigned | 物品数量 |
| itemType | int unsigned | 物品类型 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `shopType`, `pos`)，即同一玩家在某服的某类随机商店下，每个格子一条记录，支持按玩家+服+商店类型批量查询全部格子。

---

## 六、PVE 玩法

### 21. pve_planet_info_table_0（星球/关卡属性表）

**实体含义**：玩家在某个**关卡（星球）**下的扩展属性，键值对形式，value 为 JSON。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| levelId | int unsigned | 关卡 ID |
| type | varchar(128) | 属性类型 |
| value | text | 属性值，JSON |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `levelId`, `type`)，同一玩家在某关卡下、某属性 type 一条记录。

---

### 22. pve_planet_spt_info_table_0（星球/关卡据点属性表）

**实体含义**：玩家在某个关卡的**据点（spt）**下的扩展属性，结构同 pve_planet_info_table_0。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| levelId | int unsigned | 关卡 ID |
| type | varchar(128) | 属性类型 |
| value | text | 属性值，JSON |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `levelId`, `type`)，同一玩家在某关卡据点下、某属性 type 一条记录。

---

### 23. pve_expedition_table_0（星际远征表）

**实体含义**：玩家**星际远征**玩法的进度与奖励，含积分、勋章、关卡、Boss、重置等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userId | bigint | 玩家ID |
| serverId | int | 区服ID |
| type | int | 玩法类型/模式 |
| score | int | 星际积分 |
| isWin | int | 是否胜利等状态 |
| levelId | int | 当前关卡 ID |
| petList | text | 出战精灵列表，JSON |
| record | text | 历史存档，JSON |
| boxId | int | 宝箱等 ID |
| starMedal | int | 星际勋章 |
| bossPet | text | Boss 信息，JSON |
| maxLevel | int | 历史最高关卡（用于积分计算） |
| days | int | 重置次数等 |
| datetime | datetime | 最后更新时间 |
| levelInfo | text | 战斗过的关卡信息，JSON |
| resetTimes | int unsigned | 每日重置次数 |

**主键 / 索引**：
- 建表 SQL 中未定义显式主键索引，但业务上可视为按 (`userId`, `serverId`, `type`) 唯一标识某玩家在某服某种远征模式的一条记录。

---

### 24. pve_combat_ladder_table_（进击/战斗天梯表）

**实体含义**：**进击类爬塔**玩法的历史记录，按 type 区分不同赛季或模式。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| type | tinyint | 历史数据分类（如赛季） |
| heroMedal | int unsigned | 进击勋章 |
| levelId | int unsigned | 层数/关卡 ID |
| roundTimes | int | 回合总数 |
| bossInfo | text | 战斗 Boss 信息，JSON |
| isWin | int unsigned | 是否胜利 |
| score | int unsigned | 分数 |
| petList | text | 出战精灵信息，JSON |
| datetime | timestamp | 记录时间 |

**主键 / 索引**：
- 建表 SQL 中未定义显式主键；查询时通常按 (userid, svrid, type) 或按时间范围配合 type 过滤历史记录。

---

## 七、PVP 玩法

### 25. pvp_tianti_table_0（天梯/排位表）

**实体含义**：玩家**天梯排位**的积分、胜场、连胜、荣誉及阵容等。按 (userId, serverId, activityId) 区分不同天梯模式。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userId | bigint | 玩家ID |
| serverId | int | 服务器ID |
| crtWin, crtLos | int | 当前赛季胜利/失败场次 |
| crtScore | int | 当前积分 |
| pastScore | int | 历史最高积分 |
| pastWin | int | 历史最高胜场 |
| winStreak | int | 当前连胜次数 |
| honor | int | 获得荣誉值 |
| petList | text | 天梯阵容精灵列表，JSON |
| datetime | datetime | 更新时间 |
| battleTimes | int unsigned | 当日参战次数 |
| lastBattleTime | int unsigned | 最近一次参战时间 |
| oldCrtScore | int unsigned | 旧体系天体积分 |
| crtDauntlessPoints | int | 无畏积分 |
| activityId | int | 天梯模式/活动 ID |

**主键 / 索引**：
- 主键：(`userId`, `serverId`, `activityId`)，即同一服下某玩家在某个天梯模式的一条当前状态记录。支持按玩家+服批量查询所有模式或按 activityId 精确查一条。

---

### 26. pvp_melee_table_0（大乱斗/混战表）

**实体含义**：**大乱斗**玩法的积分、参与次数、荣誉等汇总。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userId | bigint | 玩家ID |
| serverId | int | 服务器ID |
| score | int | 积分 |
| count | int | 参与次数等 |
| honor | int | 荣誉值 |
| datetime | datetime | 更新时间 |

---

## 八、社交

### 27. friend_info_table_0（好友关系表）

**实体含义**：**好友/黑名单关系**。每行表示 userid 与 friendid 在 svrid 下的关系及星标、最后聊天时间。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 用户ID（主视角） |
| svrid | int unsigned | 服务器ID |
| friendid | int unsigned | 好友/对方用户ID |
| relation | int unsigned | 0 申请者，1 好友，2 黑名单 |
| starFlag | tinyint | 是否星标好友，0 否 1 是 |
| chatLastTime | int unsigned | 最近一次聊天时间 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `friendid`)，同一玩家在某服，对同一个好友/对象只有一条关系记录。

---

## 九、邮件

### 28. mail_info_table_0（邮件主表）

**实体含义**：玩家收到的**每一封邮件**，含标题、发件人、内容、附件、状态等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| mailId | int unsigned | 邮件ID，自增主键 |
| userId | int unsigned | 收件人用户ID |
| svrId | int unsigned | 收件人服务器ID |
| title | varchar(64) | 邮件标题 |
| sender | varchar(32) | 发件人 |
| sendTime | int unsigned | 发送时间（秒级时间戳） |
| mailType | int unsigned | 邮件类型 |
| content | text | 邮件正文 |
| mailState | int unsigned | 状态：未查看、已查看未领取、已查看已领取 |
| attachmentFlag | int unsigned | 是否有附件 |
| attachReceiveTime | int unsigned | 附件领取时间 |
| attachment | varchar(4096) | 附件内容（如物品 JSON） |
| addition | varchar(255) | 扩展字段 |

**主键 / 索引**：
- 主键：(`mailId`)，每封邮件一个唯一自增 ID。
- 典型查询：按 (userId, svrId, mailState, sendTime) 过滤玩家邮件列表。

---

### 29. mail_record_table_0（邮件领取记录表）

**实体含义**：玩家对**系统/配置类邮件**的领取记录，防重复领取。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userId | int unsigned | 游戏账号ID |
| svrId | int unsigned | 服务器ID |
| configId | varchar(64) | 配置邮件 ID |
| sendTime | bigint | 物品发放时间等 |
| sender | varchar(64) | 发送方标识 |
| result | varchar(64) | 领取结果等 |

**主键 / 索引**：
- 主键：(`userId`, `svrId`, `configId`, `sendTime`)，同一玩家对某配置邮件的一次发放记录唯一，用于防止重复领取。

---

## 十、任务与成就

### 30. maintask_info_table_0（主线任务表）

**实体含义**：玩家的**主线任务**进度与领取状态。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| mainTaskId | int unsigned | 主线任务 ID |
| status | tinyint | 0 未达成，1 达成未领取，2 已领取 |
| step | int unsigned | 任务进度 |
| time | int unsigned | 领取奖励时间 |
| args | int unsigned | 扩展参数 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `mainTaskId`)，同一玩家在某服、某主线任务一条记录。

---

### 31. achieve_info_table_0（成就表）

**实体含义**：玩家的**成就**完成与领取状态。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| achieveId | int unsigned | 成就 ID |
| status | tinyint | 0 未达成，1 达成未领取，2 已领取 |
| step | int unsigned | 成就进度 |
| time | int unsigned | 领取奖励时间 |
| rank | int unsigned | 排名等 |
| achieveTime | int unsigned | 达成时间 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `achieveId`)，同一玩家在某服、某成就一条记录。

---

## 十一、其他系统

### 32. attr_info_table_0（玩家属性表）

**实体含义**：玩家的**带时效的数值属性**键值对，如 buff、活动属性等。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| type | int unsigned | 属性 ID |
| value | int unsigned | 属性值 |
| startTime | int unsigned | 属性生效开始时间 |
| endTime | int unsigned | 属性失效时间 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `type`)，同一玩家在某服、某属性 ID 的一条带时效属性记录。

---

### 33. bit_info_table_0（位标记表）

**实体含义**：玩家的**按位或按 key 的开关/标记**，type 为位置编号，value 为对应值。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 游戏服ID |
| type | int | 键/位编号 |
| value | int unsigned | 值 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `type`)，同一玩家在某服、某位标记 type 一条记录。

---

### 34. red_point_table_0（红点表）

**实体含义**：**红点/提示点**的显示状态与有效期。rIndex/subIndex 对应前端红点枚举。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| uid | int | 玩家ID |
| svrId | int | 服务器ID |
| rIndex | int | 红点索引（见代码枚举） |
| subIndex | int | 红点子索引 |
| status | int | 状态等 |
| validity | int | 有效期（秒），-1 永久有效（注：部分注释在导出时可能有错位，以代码为准） |
| effective | int | 扩展字段 |

**主键 / 索引**：
- 主键：(`uid`, `svrId`, `rIndex`, `subIndex`)，同一玩家在某服、某红点位一条记录。

---

### 35. carnival_info_table_0（嘉年华/活动表）

**实体含义**：玩家在某个**嘉年华/活动**中的进度与完成状态。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| carnivalId | int unsigned | 嘉年华/活动 ID |
| status | tinyint | 状态 |
| step | int unsigned | 进度 |
| finishTime | int unsigned | 完成时间 |
| svrid | int unsigned | 服务器ID |

**主键 / 索引**：
- 主键：(`userid`, `carnivalId`, `svrid`)，同一玩家在某服、某个嘉年华活动一条记录。

---

### 36. fight_puni_snapshot_0（战斗惩罚/断线快照表）

**实体含义**：**战斗断线重连**用的快照数据，用于恢复战斗现场。

| 字段名 | 类型 | 含义 |
|--------|------|------|
| userid | int unsigned | 玩家ID |
| svrid | int unsigned | 服务器ID |
| snapshotid | int unsigned | 快照 ID |
| stat | int unsigned | 快照状态，1 有效 |
| updateTime | timestamp | 更新时间 |
| enterBattle | mediumtext | 进入战斗所需数据 |
| extData | text | JSON，恢复战斗的额外信息 |

**主键 / 索引**：
- 主键：(`userid`, `svrid`, `snapshotid`)，同一玩家在某服的某个战斗快照唯一记录。

---

## 表关系与常用关联

- **玩家标识**：多数表用 `(userid, svrid)` 或 `(userId, serverId)` 表示“某服某号”。
- **精灵唯一键**：`pet_info_table_0` 中为 `(userid, svrid, getTime)`；刻印为 `(userid, svrid, tokenGetTime)`。
- **战队**：`team_base_info_table_0.teamId` 为战队主键；`team_member_info_table_0`、`team_applier_info_table_0`、`team_attr_info_table_0`、`team_equip_info_table_0`、`team_event_info_table_0` 均通过 `teamId` 关联。
- **邮件**：`mail_info_table_0.mailId` 为邮件主键；`mail_record_table_0` 按 configId 记录配置类邮件领取。
- **商店**：`shop_info_table_0.idx` 对应配置表 shopMass；随机商店用 `(userid, svrid, shopType, pos)` 定位格子。

以上描述可直接用于 GraphRAG 建图与 Text2SQL 的 schema 理解；若与实际代码或配置有出入，请以代码与配置为准并手动修正本文档。
