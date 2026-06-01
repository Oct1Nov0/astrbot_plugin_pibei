import os
import json
import sqlite3
import datetime
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ============ 配置区 ============
# 管理员 QQ 号不写在代码里，而是放在插件目录下的 admins.json（不上传 GitHub）。
# 文件格式：{"admins": ["QQ号1", "QQ号2"]}
# 首次运行若文件不存在，会自动生成一个空模板，请填好后用 /重载管理员 或重启生效。

# 第一版固定分类
DEFAULT_CATEGORIES = [
    "团长骗钱",
    "排谷后跑单",
    "故意出盗版谷",
    "伪造凭证",
    "代购骗钱",
    "其他",
]
# ================================


@register("pibei", "you", "吃谷圈云黑避雷库", "1.0.0", "")
class PibeiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 数据库放在插件自己的数据目录下
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.plugin_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "pibei.db")
        self.admins_path = os.path.join(self.plugin_dir, "admins.json")
        self.admins = self._load_admins()
        self._init_db()
        logger.info(f"[避雷库] 插件已加载，管理员{len(self.admins)}人，数据库：{self.db_path}")

    def _load_admins(self):
        # 从 admins.json 读管理员QQ列表，文件不存在则生成空模板
        if not os.path.exists(self.admins_path):
            with open(self.admins_path, "w", encoding="utf-8") as f:
                json.dump({"admins": []}, f, ensure_ascii=False, indent=2)
            logger.warning(f"[避雷库] 未找到 admins.json，已生成空模板，请填入管理员QQ：{self.admins_path}")
            return []
        try:
            with open(self.admins_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [str(q) for q in data.get("admins", [])]
        except Exception as e:
            logger.error(f"[避雷库] 读取 admins.json 失败：{e}")
            return []

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._conn()
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS risk_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT UNIQUE,
            qq TEXT,
            nickname TEXT,
            category TEXT,
            amount REAL,
            description TEXT,
            evidence_images TEXT,
            reporter_qq TEXT,
            group_id TEXT,
            status TEXT,
            reviewer_qq TEXT,
            created_at TEXT,
            reviewed_at TEXT,
            updated_at TEXT
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            enabled INTEGER DEFAULT 1
        )
        """)
        # 初始化分类
        for name in DEFAULT_CATEGORIES:
            c.execute(
                "INSERT OR IGNORE INTO categories (name, enabled) VALUES (?, 1)",
                (name,),
            )
        conn.commit()
        conn.close()

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return str(event.get_sender_id()) in self.admins

    def _gen_case_id(self, conn) -> str:
        today = datetime.datetime.now().strftime("%Y%m%d")
        prefix = f"C{today}"
        c = conn.cursor()
        c.execute(
            "SELECT case_id FROM risk_cases WHERE case_id LIKE ? ORDER BY case_id DESC LIMIT 1",
            (prefix + "%",),
        )
        row = c.fetchone()
        if row:
            last_seq = int(row["case_id"][-4:])
            seq = last_seq + 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"

    def _now(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---------- 录入 ----------
    @filter.command("录入")
    async def add_case(self, event: AstrMessageEvent):
        """录入避雷记录。格式：/录入 QQ号 分类 金额 事件经过"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员才能录入避雷记录。")
            return

        # 解析参数：去掉指令名后按空格切分
        text = event.message_str.strip()
        parts = text.split(maxsplit=4)
        # parts[0] 是 "录入"
        if len(parts) < 5:
            yield event.plain_result(
                "格式不对。正确格式：\n"
                "/录入 QQ号 分类 金额 事件经过\n"
                "例如：\n"
                "/录入 123456 团长骗钱 1200 收了钱不发货，拉黑\n"
                "金额没有就填 0。分类见 /分类列表"
            )
            return

        qq = parts[1].strip()
        category = parts[2].strip()
        amount_raw = parts[3].strip()
        description = parts[4].strip()

        if not qq.isdigit():
            yield event.plain_result("QQ号必须是数字，请检查。")
            return

        try:
            amount = float(amount_raw)
        except ValueError:
            yield event.plain_result("金额必须是数字（没有就填0），请检查。")
            return

        conn = self._conn()
        # 校验分类是否存在且启用
        c = conn.cursor()
        c.execute("SELECT name FROM categories WHERE name=? AND enabled=1", (category,))
        if not c.fetchone():
            conn.close()
            cats = "、".join(DEFAULT_CATEGORIES)
            yield event.plain_result(
                f"分类「{category}」不存在。可用分类：\n{cats}\n（可用 /添加分类 名称 来新增）"
            )
            return

        case_id = self._gen_case_id(conn)
        now = self._now()
        try:
            group_id = event.get_group_id()
        except Exception:
            group_id = ""

        c.execute(
            """INSERT INTO risk_cases
            (case_id, qq, nickname, category, amount, description, evidence_images,
             reporter_qq, group_id, status, reviewer_qq, created_at, reviewed_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                case_id, qq, "", category, amount, description, "[]",
                str(event.get_sender_id()), str(group_id), "已通过",
                str(event.get_sender_id()), now, now, now,
            ),
        )
        conn.commit()
        conn.close()

        yield event.plain_result(
            f"录入成功，已生效。\n"
            f"案件号：{case_id}\n"
            f"QQ：{qq}\n"
            f"分类：{category}\n"
            f"金额：{amount:g}元\n"
            f"状态：已通过"
        )

    # ---------- 查询 ----------
    @filter.command("查")
    async def query_case(self, event: AstrMessageEvent):
        """查询某个QQ的避雷记录。格式：/查 QQ号"""
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            yield event.plain_result("格式：/查 QQ号\n例如：/查 123456")
            return

        qq = parts[1].strip()
        conn = self._conn()
        c = conn.cursor()
        c.execute(
            "SELECT * FROM risk_cases WHERE qq=? AND status='已通过' ORDER BY created_at DESC",
            (qq,),
        )
        rows = c.fetchall()
        conn.close()

        if not rows:
            yield event.plain_result(f"未查询到 QQ {qq} 的有效避雷记录。")
            return

        lines = [f"⚠️ 风险记录", f"QQ：{qq}", f"有效记录：{len(rows)}条", ""]
        for r in rows:
            amt = f"{r['amount']:g}元" if r["amount"] else "—"
            lines.append(f"【{r['case_id']}】{r['category']}")
            lines.append(f"  金额：{amt}")
            lines.append(f"  经过：{r['description']}")
            lines.append(f"  时间：{r['created_at'][:10]}")
            lines.append("")
        yield event.plain_result("\n".join(lines).strip())

    # ---------- 撤销 ----------
    @filter.command("撤销")
    async def revoke_case(self, event: AstrMessageEvent):
        """撤销一条避雷记录。格式：/撤销 案件号"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员才能撤销记录。")
            return

        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("格式：/撤销 案件号\n例如：/撤销 C202606010001")
            return

        case_id = parts[1].strip()
        conn = self._conn()
        c = conn.cursor()
        c.execute("SELECT * FROM risk_cases WHERE case_id=?", (case_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            yield event.plain_result(f"未找到案件号 {case_id}。")
            return
        if row["status"] == "已撤销":
            conn.close()
            yield event.plain_result(f"案件 {case_id} 已经是撤销状态了。")
            return

        c.execute(
            "UPDATE risk_cases SET status='已撤销', updated_at=? WHERE case_id=?",
            (self._now(), case_id),
        )
        conn.commit()
        conn.close()
        yield event.plain_result(f"案件 {case_id} 已撤销（记录保留，不会删除）。")

    # ---------- 案件详情 ----------
    @filter.command("详情")
    async def case_detail(self, event: AstrMessageEvent):
        """查看某条案件的完整信息。格式：/详情 案件号"""
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("格式：/详情 案件号")
            return
        case_id = parts[1].strip()
        conn = self._conn()
        c = conn.cursor()
        c.execute("SELECT * FROM risk_cases WHERE case_id=?", (case_id,))
        r = c.fetchone()
        conn.close()
        if not r:
            yield event.plain_result(f"未找到案件号 {case_id}。")
            return
        amt = f"{r['amount']:g}元" if r["amount"] else "—"
        yield event.plain_result(
            f"案件号：{r['case_id']}\n"
            f"QQ：{r['qq']}\n"
            f"分类：{r['category']}\n"
            f"金额：{amt}\n"
            f"经过：{r['description']}\n"
            f"状态：{r['status']}\n"
            f"录入人：{r['reporter_qq']}\n"
            f"录入时间：{r['created_at']}"
        )

    # ---------- 分类列表 ----------
    @filter.command("分类列表")
    async def list_categories(self, event: AstrMessageEvent):
        """查看所有可用分类"""
        conn = self._conn()
        c = conn.cursor()
        c.execute("SELECT name FROM categories WHERE enabled=1 ORDER BY id")
        rows = c.fetchall()
        conn.close()
        names = "\n".join(f"· {r['name']}" for r in rows)
        yield event.plain_result(f"可用分类：\n{names}")

    # ---------- 添加分类 ----------
    @filter.command("添加分类")
    async def add_category(self, event: AstrMessageEvent):
        """新增一个分类。格式：/添加分类 名称"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员才能添加分类。")
            return
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("格式：/添加分类 名称")
            return
        name = parts[1].strip()
        conn = self._conn()
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO categories (name, enabled) VALUES (?, 1)", (name,))
        c.execute("UPDATE categories SET enabled=1 WHERE name=?", (name,))
        conn.commit()
        conn.close()
        yield event.plain_result(f"分类「{name}」已添加。")

    # ---------- 帮助 ----------
    @filter.command("重载管理员")
    async def reload_admins(self, event: AstrMessageEvent):
        """重新读取 admins.json。格式：/重载管理员"""
        if not self._is_admin(event):
            yield event.plain_result("只有现任管理员才能重载管理员列表。")
            return
        self.admins = self._load_admins()
        yield event.plain_result(f"已重载，当前管理员 {len(self.admins)} 人。")

    @filter.command("避雷帮助")
    async def pibei_help(self, event: AstrMessageEvent):
        """黑名单使用帮助"""
        yield event.plain_result(
            "【黑名单 指令】\n"
            "查询（所有人可用）：\n"
            "  /查 QQ号\n"
            "  /详情 案件号\n"
            "  /分类列表\n"
            "管理员专用：\n"
            "  /录入 QQ号 分类 金额 事件经过\n"
            "  /撤销 案件号\n"
            "  /添加分类 名称\n"
            "例：/录入 123456 团长骗钱 1200 收钱不发货"
        )

    async def terminate(self):
        logger.info("[避雷库] 插件已卸载")
