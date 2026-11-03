import json
import sys

from . import config


def main():
    cmd, rest = (sys.argv[1] if len(sys.argv) > 1 else "service"), sys.argv[2:]
    if cmd == "service":
        from .service import run
        run()
    elif cmd == "job":
        from .job import main as job
        job(rest)
    elif cmd == "baseline":
        from .job import baseline
        sys.exit(baseline(rest))
    elif cmd == "rerun":
        from .rerun import main as rerun
        sys.exit(rerun(rest))
    elif cmd == "impact":
        from .impact import main as impact
        sys.exit(impact(rest))
    elif cmd == "approve":
        from .job import approve
        sys.exit(approve(rest))
    elif cmd == "seed":
        from .seed import main as seed
        seed(rest)
    elif cmd == "shadow":
        from .shadow import main as shadow
        sys.exit(shadow(rest))
    elif cmd == "coverage":
        from .coverage import main as coverage
        sys.exit(coverage(rest))
    elif cmd == "recon":
        from .recon import main as recon
        sys.exit(recon(rest))
    elif cmd == "quality":
        from .quality import main as quality
        sys.exit(quality(rest))
    elif cmd == "segment":
        from . import dictionary
        from .pipeline import segment_checked
        dic = dictionary.load()
        for line in sys.stdin:
            post = json.loads(line)
            terms, bad = segment_checked(dic, post.get("channel", ""), post.get("text", ""))
            print(json.dumps({"post_id": post.get("post_id"), "pipeline": dic.pipeline, "dict_version": dic.version,
                              "terms": terms, "must_keep_split": bad}, ensure_ascii=False))
    else:
        sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
