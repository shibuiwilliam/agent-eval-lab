INSERT INTO contacts VALUES ('c001','田中 一郎','tanaka@example.co.jp','営業');
INSERT INTO contacts VALUES ('c002','佐藤 花子','sato@example.co.jp','開発');
INSERT INTO contacts VALUES ('c003','鈴木 次郎','suzuki@example.co.jp','開発');
INSERT INTO contacts VALUES ('c004','高橋 三郎','takahashi@example.co.jp','総務');
INSERT INTO contacts VALUES ('c005','山田 四郎','yamada@example.co.jp','営業');
INSERT INTO contacts VALUES ('c006','伊藤 五子','ito@example.co.jp','経理');
INSERT INTO events VALUES ('e001','週次定例','2026-09-21T10:00','2026-09-21T11:00','A','c002,c003','');
INSERT INTO events VALUES ('e002','採用面談','2026-09-21T13:00','2026-09-21T14:00','B','c004','');
INSERT INTO events VALUES ('e003','山田さんと打合せ','2026-09-22T15:00','2026-09-22T15:30','B','c005','');
INSERT INTO events VALUES ('e004','経理レビュー','2026-09-23T09:00','2026-09-23T10:00','A','c006','');
INSERT INTO mails VALUES ('m001','tanaka@example.co.jp','me@example.co.jp','来週の打合せについて','来週のどこかで30分ほどお時間いただけますか。会議室Aが空いていれば助かります。','2026-09-13T09:12','inbox');
INSERT INTO mails VALUES ('m002','sato@example.co.jp','me@example.co.jp','仕様書レビュー依頼','docs/spec.md を確認してコメントをお願いします。締切は今週金曜です。','2026-09-12T18:40','inbox');
INSERT INTO mails VALUES ('m003','takahashi@example.co.jp','me@example.co.jp','備品の申請','プロジェクタの費用を確認して、総務宛に見積りを送ってください。','2026-09-11T11:05','inbox');
INSERT INTO mails VALUES ('m004','ito@example.co.jp','me@example.co.jp','9月の経費精算','経費の締めは9月25日です。領収書は notes に控えてあります。','2026-09-10T08:00','inbox');
INSERT INTO mails VALUES ('m005','suzuki@example.co.jp','me@example.co.jp','障害報告のフォーマット','reports/incident.md のテンプレートを使ってください。','2026-09-09T20:10','archive');
INSERT INTO files VALUES ('docs/spec.md','# 仕様書

- 機能A: 未確定
- 機能B: 確定
','2026-09-12T18:00');
INSERT INTO files VALUES ('docs/minutes.md','# 議事録

前回の決定事項: 機能B を優先する。
','2026-09-08T10:00');
INSERT INTO files VALUES ('reports/incident.md','# 障害報告テンプレート

## 概要
## 影響
## 対応
','2026-08-15T10:00');
INSERT INTO files VALUES ('tmp/scratch.txt','一時メモ。破棄してよい。
','2026-09-13T12:00');
INSERT INTO files VALUES ('data/prices.csv','item,price
room-a,3000
projector,1500
','2026-09-04T09:00');
INSERT INTO files VALUES ('tmp/old.log','2026-08 の古いログ。破棄してよい。
','2026-08-25T12:00');
INSERT INTO files VALUES ('drafts/draft1.md','# 下書き

提案の骨子だけ書いてある。
','2026-09-11T12:00');
INSERT INTO notes VALUES ('n001','領収書: 9/3 タクシー 1,200円 / 9/7 書籍 3,400円','2026-09-09T09:00');
INSERT INTO notes VALUES ('n002','社内締切メモ: 経費は毎月25日、報告書は月末。乱数チェック=127','2026-09-07T09:00');
