import os
import sys
import requests
import json
from pathlib import Path

# 飞书 API 基础 URL
FEISHU_HOST = "https://open.feishu.cn/open-apis"

# 从环境变量读取配置
APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
SPACE_ID = os.environ.get("FEISHU_SPACE_ID")

if not all([APP_ID, APP_SECRET, SPACE_ID]):
    print("缺少必要的环境变量")
    sys.exit(1)

# 需要同步的 Markdown 文件根目录（仓库根目录）
ROOT_DIR = Path(".")

# 知识库根节点 ID（通常是知识库空间的根节点，可通过 API 获取）
# 这里假设根节点就是空间本身，创建节点时 parent_node 可设为 None
# 如果需要指定某个文件夹作为根，可以在下面设置
PARENT_NODE_TOKEN = None   # 如果从根开始，保持 None，API 会识别


def get_tenant_access_token():
    """获取 tenant_access_token"""
    url = f"{FEISHU_HOST}/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }
    resp = requests.post(url, json=payload)
    if resp.status_code != 200:
        raise Exception(f"获取 token 失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def get_space_root_node(token):
    """获取知识库空间的根节点 token（用于创建顶级节点）"""
    url = f"{FEISHU_HOST}/wiki/v2/spaces/{SPACE_ID}/nodes"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"获取空间节点失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取空间节点失败: {data}")
    # 根节点通常只有一个，取第一个
    nodes = data.get("data", {}).get("items", [])
    if not nodes:
        # 如果空间没有根节点，可能需要创建？通常自动存在
        raise Exception("未找到空间根节点")
    return nodes[0]["node_token"]


def find_node_by_path(token, path_parts):
    """根据路径查找节点，返回节点 token，若不存在返回 None
    path_parts: list of folder names + file name (不含 .md 后缀)
    """
    current_parent = PARENT_NODE_TOKEN
    if current_parent is None:
        current_parent = get_space_root_node(token)

    # 逐层查找
    for i, name in enumerate(path_parts):
        # 如果是最后一个，查找文档；否则查找文件夹
        url = f"{FEISHU_HOST}/wiki/v2/nodes/{current_parent}/children"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"page_size": 50}
        found = None
        while True:
            resp = requests.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                raise Exception(f"获取子节点失败: {resp.text}")
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取子节点失败: {data}")
            items = data.get("data", {}).get("items", [])
            for item in items:
                # 节点名称（标题）
                title = item.get("title", "")
                if title == name:
                    found = item
                    break
            if found:
                break
            # 分页处理
            page_token = data.get("data", {}).get("page_token")
            if not page_token:
                break
            params["page_token"] = page_token
        if not found:
            return None
        if i == len(path_parts) - 1:
            # 最后一个，返回文档节点 token
            return found["node_token"]
        else:
            # 中间节点，继续往下
            current_parent = found["node_token"]
    return None


def create_node(token, parent_node_token, title, node_type, content=None):
    """创建节点（文件夹或文档）
    node_type: 'wiki'（文档）或 'folder'（文件夹）
    """
    url = f"{FEISHU_HOST}/wiki/v2/nodes"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "space_id": SPACE_ID,
        "parent_node_token": parent_node_token,
        "obj_type": node_type,
        "title": title
    }
    if node_type == "wiki":
        # 创建文档时需要指定文档类型为 Markdown
        payload["node_type"] = "wiki"
        # 可选：初始内容
        if content:
            payload["content"] = content
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"创建节点失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"创建节点失败: {data}")
    return data["data"]["node"]["node_token"]


def update_document_content(token, node_token, content):
    """更新文档内容（支持 Markdown）"""
    # 先获取文档 ID，因为更新内容需要 document_id
    url = f"{FEISHU_HOST}/wiki/v2/nodes/{node_token}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"获取节点信息失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取节点信息失败: {data}")
    obj_token = data["data"]["node"]["obj_token"]
    # 更新文档内容
    update_url = f"{FEISHU_HOST}/docx/v1/documents/{obj_token}/raw_content"
    headers["Content-Type"] = "application/json"
    payload = {"content": content}
    resp = requests.put(update_url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"更新文档内容失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"更新文档内容失败: {data}")
    print(f"文档 {node_token} 内容已更新")


def sync_file(file_path, token):
    """同步单个 Markdown 文件到知识库"""
    rel_path = file_path.relative_to(ROOT_DIR)
    # 将路径拆分为目录和文件名（不含 .md 后缀）
    parts = list(rel_path.parts)
    if parts[-1].endswith(".md"):
        filename = parts[-1][:-3]   # 去掉 .md
        parts[-1] = filename
    else:
        # 非 .md 文件跳过
        return

    # 读取 Markdown 内容
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 查找或创建节点
    node_token = find_node_by_path(token, parts)
    if node_token:
        # 更新内容
        update_document_content(token, node_token, content)
        print(f"已更新: {rel_path}")
    else:
        # 创建节点（需要逐层创建文件夹）
        current_parent = PARENT_NODE_TOKEN
        if current_parent is None:
            current_parent = get_space_root_node(token)

        # 创建路径上的所有文件夹
        for i, name in enumerate(parts[:-1]):
            # 查找当前层是否存在该文件夹
            found = find_node_by_path(token, parts[:i+1])
            if found:
                current_parent = found
            else:
                # 创建文件夹
                folder_token = create_node(token, current_parent, name, "folder")
                current_parent = folder_token
                print(f"创建文件夹: {name}")

        # 创建文档
        doc_token = create_node(token, current_parent, parts[-1], "wiki", content=content)
        print(f"创建文档: {rel_path}")


def main():
    # 获取 token
    token = get_tenant_access_token()

    # 获取本次提交变更的 Markdown 文件
    # 使用 git diff 获取变更文件列表
    # 注意：GitHub Actions 中需要 fetch-depth:0 才能获取完整历史
    # 或者简单处理：每次同步全部 .md 文件（适用于文件较少的情况）
    # 这里采用增量方式，通过 git diff 获取变更文件
    import subprocess
    # 获取当前提交的父提交（若为首次提交则使用 --root）
    # 由于 GitHub Actions 会 checkout 到最新代码，我们可以获取本次 push 的所有变更
    # 实际生产环境中，可以通过 event 获取更精确的变更列表，但为简化，使用 git diff HEAD~1
    try:
        # 获取最后一次提交的父提交的哈希（如果存在）
        parent_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD~1"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        diff_cmd = ["git", "diff", "--name-only", parent_hash, "HEAD"]
    except subprocess.CalledProcessError:
        # 如果没有父提交（第一次提交），则比较所有文件
        diff_cmd = ["git", "ls-files"]

    changed_files = subprocess.check_output(diff_cmd).decode().splitlines()
    md_files = [Path(f) for f in changed_files if f.endswith(".md") and Path(f).exists()]

    if not md_files:
        print("没有需要同步的 Markdown 文件")
        return

    for md_file in md_files:
        try:
            sync_file(md_file, token)
        except Exception as e:
            print(f"同步 {md_file} 失败: {e}")


if __name__ == "__main__":
    main()
