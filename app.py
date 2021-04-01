import pulumi
from pulumi.x import automation as auto
from pulumi_aws import s3
from flask import Flask, request, make_response, jsonify, flash, render_template, url_for, redirect


def ensure_plugins():
    ws = auto.LocalWorkspace()
    ws.install_plugin("aws", "v3.23.0")


ensure_plugins()
app = Flask(__name__)
app.secret_key = "secret"
project_name = "pulumi_over_http"


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


@app.route("/sites/new", methods=["GET", "POST"])
def create_site():
    """creates new sites"""
    if request.method == "POST":
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
            flash(f"Successfully created site '{stack_name}'", category="info")
        except auto.StackAlreadyExistsError:
            flash(f"Error: Site with name '{stack_name}' already exists, pick a unique name", category="error")
        except Exception as exn:
            flash(str(exn))

    return render_template("create.html")


@app.route("/sites", methods=["GET"])
def list_sites():
    """lists all sites"""
    sites = []
    try:
        ws = auto.LocalWorkspace(project_settings=auto.ProjectSettings(name=project_name, runtime="python"))
        all_stacks = ws.list_stacks()
        for stack in all_stacks:
            stack = auto.select_stack(stack_name=stack.name,
                                      project_name=project_name,
                                      # no-op program, just to get outputs
                                      program=lambda *args: None)
            outs = stack.outputs()
            sites.append({"name": stack.name, "url": outs["website_url"].value})
    except Exception as exn:
        flash(str(exn))

    return render_template("index.html", sites=sites)


@app.route("/sites/<string:id>", methods=["UPDATE"])
def update_site(id: str):
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


@app.route("/sites/<string:id>/delete", methods=["POST"])
def delete_site(id: str):
    stack_name = id
    try:
        stack = auto.select_stack(stack_name=stack_name,
                                  project_name=project_name,
                                  # noop program for destroy
                                  program=lambda *args: None)
        stack.destroy(on_output=print)
        stack.workspace.remove_stack(stack_name)
        flash(f"Site '{stack_name}' successfully deleted!", category="info")
    except auto.ConcurrentUpdateError:
        flash(f"site '{stack_name}' already has update in progress", category="error")
    except Exception as exn:
        flash(str(exn))

    return redirect(url_for("list_sites"))
