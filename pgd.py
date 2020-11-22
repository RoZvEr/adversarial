import torch
from pgd_attack_steps import LinfStep, L2Step
import random
from adversarial_transfer_models import get_models_dict
from transformations import get_transformation
import argparse
import datetime
import torchvision

TARGETED_CLASS = 934
MODELS_DICT = get_models_dict()


def get_current_time():
    return str(datetime.datetime.now().strftime('%d-%m-%Y_%H:%M:%S'))


def get_random_transformation():
    transformation_types_list = ['rotation', 'noise', 'light', 'translation']
    transformation_type = random.choice(transformation_types_list)

    t = get_transformation(transformation_type)
    t.set_random_parameter()

    return t


class Attacker:
    def __init__(self, model, args, attack_step=LinfStep, masks_batch=None):
        self.model = model
        self.args = args

        if args.transfer:
            self.loss = self.transfer_loss
            self.surrogate_models = [MODELS_DICT[model_key].cuda().eval() for model_key in MODELS_DICT.keys()]
        else:
            self.loss = self.normal_loss

        if args.norm == 'l2':
            attack_step = L2Step

        self.masks_batch = masks_batch
        self.criterion = torch.nn.CrossEntropyLoss(reduction='none')
        self.attack_step = attack_step

    def __call__(self, image, mask, target, random_start=False):
        best_loss = None
        best_x = None

        step = self.attack_step(image, self.args.eps, self.args.step_size)

        if random_start:
            image = step.random_perturb(image, mask)

        label = torch.argmax(target).view(1)
        if self.args.targeted:
            label[0] = target

        x = image.clone().detach().requires_grad_(True)

        for _ in range(self.args.num_iterations):
            t = get_random_transformation()
            x = x.clone().detach().requires_grad_(True)

            if self.args.eot:
                loss = self.loss(t(x.cuda()).cpu(), label)
            else:
                loss = self.loss(x, label)

            loss.backward()

            grads = x.grad.detach().clone()
            x.grad.zero_()

            grads_with_mask = grads*mask

            if best_loss is not None:
                if best_loss < loss:
                    best_loss = loss
                    best_x = x.clone().detach()
            else:
                best_loss = loss.clone().detach()
                best_x = x.clone().detach()

            x = step.step(x, grads_with_mask)
            x = step.project(x)

        return best_x

    def normal_loss(self, x, label):
        prediction = self.model(x.unsqueeze(0))

        optimization_direction = 1

        if self.args.targeted:
            optimization_direction = -1

        return optimization_direction * self.criterion(prediction, label)

    def transfer_loss(self, x, label):
        optimization_direction = 1

        if self.args.targeted:
            optimization_direction = -1

        loss = torch.zeros([1], device='cuda')

        for current_model in self.surrogate_models:
            prediction = current_model(x.unsqueeze(0))
            current_loss = self.criterion(prediction, label)

            loss = torch.add(loss, optimization_direction * current_loss)

        loss = loss / len(self.surrogate_models)
        return loss


def main():
    time = get_current_time()

    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str, default='resnet50')
    parser.add_argument('--dataset', type=str, default='dataset/imagenet-airplanes-images.pt')
    parser.add_argument('--masks', default=False, action='store_true')
    parser.add_argument('--eps', type=float, default=8)
    parser.add_argument('--norm', type=str, default='linf')
    parser.add_argument('--step_size', type=float, default=1)
    parser.add_argument('--num_iterations', type=int, default=10)
    parser.add_argument('--targeted', default=False, action='store_true')
    parser.add_argument('--eot', default=False, action='store_true')
    parser.add_argument('--transfer', default=False, action='store_true')
    parser.add_argument('--save_file_name', type=str, default='results/pgd_new_experiments/pgd-' + time + '.pt')
    args = parser.parse_args()

    args.eps, args.step_size = args.eps / 255.0, args.step_size / 255.0

    model = torchvision.models.resnet50(pretrained=True).cuda().eval()

    attacker = Attacker(model, args)
    target = torch.cuda.FloatTensor([TARGETED_CLASS])

    if args.masks:
        images_and_masks = torch.load(args.dataset)
    else:
        images = torch.load(args.dataset)
        masks = [torch.ones((3, images[0].size(1), images[0].size(2)), device='cuda')]*images.__len__()
        images_and_masks = zip(images, masks)

    adversarial_examples_list = []
    predictions_list = []

    for image, mask in images_and_masks:
        image = image.cuda()
        mask = mask.cuda()

        original_prediction = model(image.unsqueeze(0))

        if not args.targeted:
            target = original_prediction.cuda()

        adversarial_example = attacker(image, mask[0], target, False)
        adversarial_prediction = model(adversarial_example.unsqueeze(0))

        adversarial_examples_list.append(adversarial_example.cpu())
        predictions_list.append({'original': original_prediction.cpu(),
                                 'adversarial': adversarial_prediction.cpu()})

    torch.save({'adversarial_examples': adversarial_examples_list,
                'predictions': predictions_list,
                'args': args},
               args.save_file_name)


if __name__ == '__main__':
    main()
