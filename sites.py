import functools
import pulumi
from pulumi.x import automation as auto
from pulumi_aws import s3
from flask import (Blueprint, current_app, flash, g, render_template, request, url_for, make_response, jsonify)

bp = Blueprint("sites", __name__, url_prefix="/sites")

project_name = current_app.config["PROJECT_NAME"]


# This function defines our pulumi s3 static website in terms of the content that the caller passes in.
# This allows us to dynamically deploy websites based on user defined values from the POST body.
def create_pulumi_program(content: str):
    # Create a bucket and expose a website index document
    site_bucket = s3.Bucket("s3-website-bucket", website=s3.BucketWebsiteArgs(index_document="index.html"))
    index_content = content

    # Write our index.html into the site bucket
    s3.BucketObject("index",
                    bucket=site_bucket.id,
                    content=index_content,
                    key="index.html",
                    content_type="text/html; charset=utf-8")

    # Set the access policy for the bucket so all objects are readable
    s3.BucketPolicy("bucket-policy",
                    bucket=site_bucket.id,
                    policy={
                        "Version": "2012-10-17",
                        "Statement": {
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": ["s3:GetObject"],
                            # Policy refers to bucket explicitly
                            "Resource": [pulumi.Output.concat("arn:aws:s3:::", site_bucket.id, "/*")]
                        },
                    })

    # Export the website URL
    pulumi.export("website_url", site_bucket.website_endpoint)


@bp.route("/new", methods=["POST"])
def create():
    """creates new sites"""
    stack_name = request.form.get("site-id")
    content = request.form.get("site-content")

    def pulumi_program():
        return create_pulumi_program(content)

    try:
        # create a new stack, generating our pulumi program on the fly from the POST body
        stack = auto.create_stack(stack_name=stack_name,
                                  project_name=project_name,
                                  program=pulumi_program)
        stack.set_config("aws:region", auto.ConfigValue("us-west-2"))
        # deploy the stack, tailing the logs to stdout
        up_res = stack.up(on_output=print)
    except auto.StackAlreadyExistsError:
        flash(f"Site with name '{stack_name}' already exists")
    except Exception as exn:
        flash(str(exn))

    return render_template("create.html")


@bp.route("/", methods=["GET"])
def list_handler():
    """lists all sites"""
    try:
        ws = auto.LocalWorkspace(project_settings=auto.ProjectSettings(name=project_name, runtime="python"))
        stacks = ws.list_stacks()
        return jsonify(ids=[stack.name for stack in stacks])
    except Exception as exn:
        return make_response(str(exn), 500)


@bp.route("/<string:id>", methods=["GET"])
def get_handler(id: str):
    stack_name = id
    try:
        stack = auto.select_stack(stack_name=stack_name,
                                  project_name=project_name,
                                  # no-op program, just to get outputs
                                  program=lambda *args: None)
        outs = stack.outputs()
        return jsonify(id=stack_name, url=outs["website_url"].value)
    except auto.StackNotFoundError:
        return make_response(f"stack '{stack_name}' does not exist", 404)
    except Exception as exn:
        return make_response(str(exn), 500)


@bp.route("/<string:id>", methods=["UPDATE"])
def update_handler(id: str):
    stack_name = id
    content = request.data.get('content')

    try:
        def pulumi_program():
            create_pulumi_program(content)
        stack = auto.select_stack(stack_name=stack_name,
                                  project_name=project_name,
                                  program=pulumi_program)
        stack.set_config("aws:region", auto.ConfigValue("us-west-2"))
        # deploy the stack, tailing the logs to stdout
        up_res = stack.up(on_output=print)
        return jsonify(id=stack_name, url=up_res.outputs["website_url"].value)
    except auto.StackNotFoundError:
        return make_response(f"stack '{stack_name}' does not exist", 404)
    except auto.ConcurrentUpdateError:
        return make_response(f"stack '{stack_name}' already has update in progress", 409)
    except Exception as exn:
        return make_response(str(exn), 500)


@bp.route("/<string:id>", methods=["DELETE"])
def delete_handler(id: str):
    stack_name = id
    try:
        stack = auto.select_stack(stack_name=stack_name,
                                  project_name=project_name,
                                  # noop program for destroy
                                  program=lambda *args: None)
        stack.destroy(on_output=print)
        stack.workspace.remove_stack(stack_name)
        return jsonify(message=f"stack '{stack_name}' successfully removed!")
    except auto.StackNotFoundError:
        return make_response(f"stack '{stack_name}' does not exist", 404)
    except auto.ConcurrentUpdateError:
        return make_response(f"stack '{stack_name}' already has an update in progress", 409)
    except Exception as exn:
        return make_response(str(exn), 500)
