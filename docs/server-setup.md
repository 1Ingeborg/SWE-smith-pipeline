# CPU 服务器配置记录

## 当前规格

```text
系统：Ubuntu Server 22.04 LTS x86_64
CPU：8 vCPU
内存：32 GiB
系统盘：50 GiB
数据盘：300 GiB
```

## 磁盘映射

```text
/dev/vda2 -> /       系统盘
/dev/vdb1 -> /data   数据盘，ext4
```

所有可增长的数据必须位于 `/data`。`/home/ubuntu`、`/var`、`/usr` 和 `/tmp` 默认属于系统盘。

## 数据目录

```text
/data/docker
/data/containerd
/data/repos
/data/venvs
/data/datasets
/data/huggingface
/data/tasks
/data/trajectories
/data/results
/data/cache/pip
/data/tmp
```

## 安全边界

- SSH 入站只开放 TCP 22，并限制可信来源 IP。
- 不提交 `.pem`、`.env`、API Token 或 Hugging Face Token。
- AutoDL 模型服务优先通过 SSH 隧道访问，不直接公开无认证端口。
- 本仓库不负责自动格式化磁盘，避免设备名变化时误伤系统盘。

## 检查命令

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS,UUID
df -hT
docker info --format 'DockerRootDir={{.DockerRootDir}} StorageDriver={{.Driver}}'
systemctl is-active docker containerd
```
