"""打包成 exe 时的入口脚本（PyInstaller 从这里开始分析）。效果等同于 python -m yunyun.main。

为什么不直接用 yunyun/main.py 当入口：PyInstaller 会把入口脚本所在的文件夹加进搜索路径，
那样 yunyun/ 里的 config.py、logs.py 等会被当成顶层模块 config、logs，和别的库撞名。
"""
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":   # 构建脚本检查打包结果用（见 yunyun/selfcheck.py）
        from yunyun import selfcheck
        sys.exit(selfcheck.run(sys.argv[2]))

    if len(sys.argv) >= 2 and sys.argv[1] == "--assistant-worker":   # 助手子进程（见 yunyun/assistant/worker.py）
        from yunyun.assistant import worker   # 这条路不 import Qt，子进程要轻
        worker.main(sys.argv[2:])
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == "--assistant-check":    # 设置窗口的"测试连接"（见 yunyun/assistant/check.py）
        from yunyun.assistant import check
        check.main()
        sys.exit(0)

    from yunyun.main import main
    main()
