import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from .live import evaluate_live, run_live, verify_live_run
from .provider import ProviderConfig, ProviderError
from .workflow import replay


def main():
    parser=argparse.ArgumentParser(description="B2 read-only planning and evidence workflow")
    sub=parser.add_subparsers(dest="command",required=True)
    command=sub.add_parser("replay")
    command.add_argument("--question",required=True)
    command.add_argument("--plan",required=True,type=Path)
    live=sub.add_parser("live",help="one live model-planned question")
    live.add_argument("--question",required=True)
    live.add_argument("--budget-cny",type=Decimal,default=Decimal("20"))
    live.add_argument("--target-cny",type=Decimal,default=Decimal("10"))
    evaluate=sub.add_parser("evaluate-live",help="run a project evaluation suite")
    evaluate.add_argument("--budget-cny",type=Decimal,default=Decimal("20"))
    evaluate.add_argument("--target-cny",type=Decimal,default=Decimal("10"))
    evaluate.add_argument("--suite",type=Path,default=Path("evals/b2-intent-dev.json"))
    verify=sub.add_parser("verify-live",help="verify a saved live/evaluation run without another model call")
    verify.add_argument("run",type=Path)
    args=parser.parse_args()
    try:
        if args.command=="replay": result=replay(args.question,args.plan)
        elif args.command=="verify-live": result=verify_live_run(args.run)
        else:
            config=ProviderConfig.from_env()
            result=run_live(args.question,config,args.budget_cny,args.target_cny) if args.command=="live" else evaluate_live(config,args.budget_cny,args.target_cny,suite_path=args.suite)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (ValueError,OSError,ProviderError) as error:
        print(json.dumps({"status":"rejected","error":str(error),"error_code":getattr(error,"code","configuration_or_validation_error")},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=="__main__": raise SystemExit(main())
