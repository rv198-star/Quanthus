# R9 SSH 真实校准验收

固定12例已取得**2 TRUE、10 FALSE、0 UNKNOWN**，达到至少2正2负的校准门。
两模型24份意见均完整返回；独立语义QA核验709处字面HLC引文、全部路径及四条件。
六份非决定性的派生算术或文字错误随原票留存注记，三个条件分歧保留。QA排除清单为空。

执行合同为`config/planning/r9-reference-calibration.v3.json`，实际源码
`feb82c138c30f76591b1b9610bd4441752436f70`，控制通道为Owner指定的`ssh cn02`、agentdock用户。
此前固定合成包两模型门取得2份完整意见，仅计通道诊断。所有调用显式关闭provider请求和流重试。

| 封存对象 | SHA256 |
|---|---|
| v3合同 | `8e16fc7916bde0d8c0405aaf45de1a4b5ef0810dfd67e1fd6d73dc4489dada60` |
| 选择 | `4e619a6bb52ace222aea1a2adaa20257875f42b5bbaa32c464c35f850e2302cf` |
| 24份参考 | `98cc1a6f5f6b7170da1efd50dcfdf4e1ca0d2def90078be6d7394d63c0eceadb` |
| 独立QA | `4e9e17afbfb0aff2f3c802ac6d11fc3862946cdc87a86ea0c299f7572f0d35fa` |
| 汇总 | `7c9047c2828969e25b07fa93c615d7a6d6df92f8dcd84e6aa47bbcaa7c43776e` |

私有HF `rv192/quanthus-research-r1` 固定版本
`64109016ae49800905b463b2f904d2fcea31175f`，路径
`runs/RESEARCH-R9-CALIBRATION-001/RESEARCH-R9-CALIBRATION-001-T1/SSH-CALIBRATION-003`。
148文件、1,036,616字节全部回读核对大小与SHA；发布17.661秒。

该源码在CN02/Python3.10完整`make check`为9320通过、35跳过、63 warnings，pytest935.90秒，退出0；
原始日志SHA为`dd796713b7fbc27a4b3498438c17acdd82e0d3c833dda9a8550ad19c804e3e66`。
后继学习薄驱动另以其实际源码验证。

旧80次研究CLI、本轮2次合成provider通道与24次真实校准累计106次；另2次离线loopback fixture。
原失败、原截止和累计费用继续保留。校准的fit/update均0。

采用决定：保留原四条件协议与真实正负锚，进入[独立64例新合同](../design/research-r9-prospective-s02-v1.md)的源/参考支持门。
R9后继学习、比较及S02消费者仍按实际执行验收。
