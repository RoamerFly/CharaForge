"""Independent process entry point; SQLite events work in windowed EXE too."""
import argparse,os

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--agent-worker',action='store_true');parser.add_argument('--root',required=True);parser.add_argument('--task',required=True)
    args=parser.parse_args()
    os.environ['CHARACTER_IMAGE_ROOT']=args.root
    from agent.context import Context
    from agent.runtime import run
    ctx=Context(args.root,bind=True)
    try: run(ctx,args.task)
    except Exception as e:
        import api_client
        # A duplicate process must not interrupt the task owned by the first worker.
        row=ctx.store.task(args.task)
        if row['status']=='queued': ctx.store.interrupt(args.task,api_client.clean(str(e)))
        else: ctx.store.event(args.task,'worker_rejected',{'error':api_client.clean(str(e))})
        return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
