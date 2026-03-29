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
    current_parent_token = PARENT_NODE_TOKEN  # 初始父节点，None 表示从空间根目录开始

    # 逐层查找
    for i, name in enumerate(path_parts):
        # 获取当前父节点下的子节点
        url = f"{FEISHU_HOST}/wiki/v2/spaces/{SPACE_ID}/nodes"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"page_size": 50}
        if current_parent_token:
            params["parent_node_token"] = current_parent_token
        
        found = None
        while True:
            resp = requests.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                raise Exception(f"获取节点列表失败 (HTTP {resp.status_code}): {resp.text}")
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取节点列表失败 (Code {data.get('code')}): {data.get('msg')}")
            
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
            has_more = data.get("data", {}).get("has_more", False)
            if not page_token or not has_more:
                break
            params["page_token"] = page_token
            
        if not found:
            return None
            
        # 找到当前层的节点，继续往下找
        current_parent_token = found["node_token"]
        
        if i == len(path_parts) - 1:
            # 最后一个部分，返回找到的节点 token
            return found["node_token"]
            
    return None


def create_node(token, parent_node_token, title, node_type, content=None):
    """创建节点
    node_type (作为 obj_type): 'docx'（推荐）
    """
    url = f"{FEISHU_HOST}/wiki/v2/spaces/{SPACE_ID}/nodes"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    # 根据飞书 API 校验要求，node_type (表示节点类型，origin 为原创) 是必填或建议填写的
    payload = {
        "obj_type": node_type,
        "node_type": "origin",  # 明确指定为原创节点
        "title": title
    }
    if parent_node_token:
        payload["parent_node_token"] = parent_node_token
        
    # 如果是创建文档且有内容
    if node_type == "docx" and content:
        # Wiki API 创建节点时不支持直接传内容，需要先创建再更新，
        # 或者使用特殊的导入接口。这里为了简单，先创建空文档，再调用更新接口。
        pass
        
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"创建节点失败 (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"创建节点失败 (Code {data.get('code')}): {data.get('msg')}")
    
    node_token = data["data"]["node"]["node_token"]
    
    # 如果有内容，创建后更新
    if content:
        update_document_content(token, node_token, content)
        
    return node_token


def update_document_content(token, node_token, content):
    """更新文档内容（支持 Markdown）"""
    # 先获取文档 ID，因为更新内容需要 document_id
    url = f"{FEISHU_HOST}/wiki/v2/nodes/{node_token}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"获取节点信息失败 (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取节点信息失败 (Code {data.get('code')}): {data.get('msg')}")
    
    node_data = data["data"]["node"]
    obj_token = node_data["obj_token"]
    obj_type = node_data["obj_type"]
    
    if obj_type != "docx":
        print(f"警告: 节点 {node_token} 类型为 {obj_type}，非 docx，跳过内容更新")
        return

    # 更新文档内容
    update_url = f"{FEISHU_HOST}/docx/v1/documents/{obj_token}/raw_content"
    headers["Content-Type"] = "application/json"
    payload = {"content": content}
    resp = requests.put(update_url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"更新文档内容失败 (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"更新文档内容失败 (Code {data.get('code')}): {data.get('msg')}")
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

    # 查找节点
    node_token = find_node_by_path(token, parts)
    if node_token:
        # 更新内容
        update_document_content(token, node_token, content)
        print(f"已更新: {rel_path}")
    else:
        # 创建节点（需要逐层创建文件夹）
        current_parent = PARENT_NODE_TOKEN  # 初始父节点，None 表示空间根目录

        # 创建路径上的所有文件夹
        for i, name in enumerate(parts[:-1]):
            # 查找当前层是否存在该文件夹
            found = find_node_by_path(token, parts[:i+1])
            if found:
                current_parent = found
            else:
                # 创建文件夹（Wiki 中文件夹也是一种文档，通常用 docx 即可）
                folder_token = create_node(token, current_parent, name, "docx")
                current_parent = folder_token
                print(f"创建文件夹节点: {name}")

        # 创建文档
        doc_token = create_node(token, current_parent, parts[-1], "docx", content=content)
        print(f"创建文档: {rel_path}")


def main():
    # 获取 token
    try:
        token = get_tenant_access_token()
    except Exception as e:
        print(f"身份验证失败: {e}")
        sys.exit(1)

    # 获取本次提交变更的 Markdown 文件
    import subprocess
    try:
        # 尝试获取最近一次提交变更的文件列表
        # 在 GitHub Actions 中建议设置 fetch-depth: 0
        diff_cmd = ["git", "diff", "--name-only", "HEAD~1", "HEAD"]
        changed_files = subprocess.check_output(diff_cmd, stderr=subprocess.STDOUT).decode().splitlines()
    except subprocess.CalledProcessError:
        # 如果是第一次提交或者 HEAD~1 不存在，则列出所有文件
        print("无法获取增量更新，将同步所有 Markdown 文件")
        diff_cmd = ["git", "ls-files"]
        changed_files = subprocess.check_output(diff_cmd).decode().splitlines()

    md_files = [Path(f) for f in changed_files if f.endswith(".md") and Path(f).exists()]

    if not md_files:
        print("没有需要同步的 Markdown 文件")
        return

    print(f"准备同步 {len(md_files)} 个文件...")
    for md_file in md_files:
        try:
            sync_file(md_file, token)
        except Exception as e:
            print(f"同步 {md_file} 失败: {e}")


if __name__ == "__main__":
    main()
