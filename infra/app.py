#!/usr/bin/env python3
from aws_cdk import App
from sde_stack import SdeCrawlerStack

app = App()
SdeCrawlerStack(app, "SdeCrawlerStack")
app.synth()
