#!/usr/bin/env python3
"""infrastructure/app.py — CDK app entrypoint. `cdk synth` / `cdk deploy` run this."""

import aws_cdk as cdk

from stacks.palimpsest_stack import PalimpsestStack

app = cdk.App()
PalimpsestStack(app, "PalimpsestStack")
app.synth()
